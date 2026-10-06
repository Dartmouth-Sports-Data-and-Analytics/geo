const CAMPUS = {
  Brown: [41.8268, -71.4025],
  Columbia: [40.8075, -73.9626],
  Cornell: [42.4534, -76.4735],
  Dartmouth: [43.7044, -72.2887],
  Harvard: [42.3770, -71.1167],
  Penn: [39.9522, -75.1932],
  Princeton: [40.3431, -74.6551],
  Yale: [41.3163, -72.9223]
};

// Degrees between neighbors who share a hometown (about 4px at zoom 8). It is fixed on the map, so zooming in spreads them out.
const SPREAD_DEG = 0.022;

// Sunflower spiral in degrees: the first athlete sits on the hometown and the rest fan out around it.
function spreadOffset(i) {
  if (i === 0) return [0, 0];
  const r = SPREAD_DEG * Math.sqrt(i);
  const a = i * 2.399963;
  return [r * Math.cos(a), r * Math.sin(a)];
}

const state = {
  map: null,
  markers: [],
  lines: null,
  linesRenderer: null,
  activeSchools: new Set(),
  activeYears: new Set(),
  activeSports: new Set(),
  activeRegions: null,
  regionChips: new Map()
};

async function loadData() {
  const res = await fetch("data.json?v=93c70efe");
  if (!res.ok) throw new Error("Failed to load data.json");
  return res.json();
}

function buildMap() {
  const map = L.map("map", {
    preferCanvas: true,
    zoomControl: false,
    minZoom: 3,
    maxZoom: 12,
    maxBounds: [[-85, -180], [85, 180]],
    maxBoundsViscosity: 1.0,
    worldCopyJump: false
  }).setView([39.5, -98.35], 4);

  L.control.zoom({ position: "bottomleft" }).addTo(map);

  L.tileLayer("https://tiles.stadiamaps.com/tiles/alidade_smooth/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; <a href="https://stadiamaps.com/" target="_blank">Stadia Maps</a> &copy; <a href="https://openmaptiles.org/" target="_blank">OpenMapTiles</a> &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a> contributors',
    maxZoom: 20,
    noWrap: true
  }).addTo(map);

  map.createPane("linesPane").style.zIndex = 380;
  map.getPane("linesPane").style.pointerEvents = "none";
  state.linesRenderer = L.canvas({ pane: "linesPane", padding: 0.5 });
  state.lines = L.layerGroup().addTo(map);

  return map;
}

// One marker per person (shared `person` ID); popup lists each sport's seasons.
function buildMarkers(map, data) {
  const renderer = L.canvas({ padding: 0.5 });
  const people = new Map();

  for (let i = 0; i < data.lat.length; i++) {
    const id = data.person ? data.person[i] : i;
    if (!people.has(id)) {
      people.set(id, {
        lat: data.lat[i], lng: data.lng[i],
        name: data.name[i] || "Unknown", school: data.school[i], hometown: data.hometown[i] || "",
        region: data.region ? data.region[i] : null,
        entries: []
      });
    }
    people.get(id).entries.push({ sport: data.sport[i], years: data.years[i] });
  }

  // Athletes sharing a hometown share coordinates, so each gets its own spot on a spiral.
  const groups = new Map();
  for (const p of people.values()) {
    const key = `${p.lat},${p.lng}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(p);
  }
  for (const g of groups.values()) {
    g.sort((a, b) => (a.school + a.name).localeCompare(b.school + b.name));
    g.forEach((p, i) => {
      const [dx, dy] = spreadOffset(i);
      p.dispLat = p.lat + dy;
      p.dispLng = p.lng + dx / Math.cos((p.lat * Math.PI) / 180);
    });
  }

  const markers = [];
  for (const p of people.values()) {
    // Merge duplicate-sport entries into one line.
    const bySport = new Map();
    for (const e of p.entries) {
      if (!bySport.has(e.sport)) bySport.set(e.sport, new Set());
      e.years.forEach((y) => bySport.get(e.sport).add(y));
    }
    p.entries = [...bySport]
      .map(([sport, ys]) => ({ sport, years: [...ys].sort((a, b) => a - b) }))
      .sort((a, b) => a.sport.localeCompare(b.sport));

    const color = SCHOOL_COLORS[p.school] || "#888";
    const marker = L.circleMarker([p.dispLat, p.dispLng], {
      renderer,
      radius: 7,
      color,
      fillColor: color,
      fillOpacity: 0.75,
      weight: 0,
      stroke: false
    });
    Object.assign(marker, { _school: p.school, _region: p.region, _entries: p.entries, _name: p.name, _hometown: p.hometown });

    const sportLines = p.entries.map((e) =>
      `<div class="popup-line">${escapeHtml(e.sport)} &middot; ${formatYearRanges(e.years)}</div>`
    ).join("");
    marker.bindPopup(
      `<div class="popup-name">${escapeHtml(p.name)}</div>` +
      `<div class="popup-line">${escapeHtml(p.school)}</div>` +
      sportLines +
      `<div class="popup-line">${escapeHtml(p.hometown)}</div>`
    );

    marker.addTo(map);
    markers.push(marker);
  }

  return markers;
}

function formatYearRanges(years) {
  return [...new Set(years)].sort((a, b) => a - b).map(formatSeason).join(", ");
}

// Hidden markers are removed from the map (not made transparent) so they can't be clicked.
function applyFilters() {
  const { activeSchools, activeSports, activeYears, activeRegions, map } = state;
  const shown = [];
  const regionCounts = {};

  for (const m of state.markers) {
    const matches = activeSchools.has(m._school) &&
      m._entries.some((e) => activeSports.has(e.sport) && e.years.some((y) => activeYears.has(y)));
    if (matches && m._region) regionCounts[m._region] = (regionCounts[m._region] || 0) + 1;

    if (matches && (!activeRegions || activeRegions.has(m._region))) {
      if (!map.hasLayer(m)) m.addTo(map);
      shown.push(m);
    } else if (map.hasLayer(m)) {
      m.closePopup();
      map.removeLayer(m);
    }
  }

  byId("count").textContent = shown.length.toLocaleString();
  updateRegionCounts(regionCounts);
  updateLines(shown);
}

// Lines from campus to every shown hometown; only drawn when the toggle is on and exactly one school is active.
function updateLines(shown) {
  const toggle = byId("linesToggle");
  state.lines.clearLayers();
  const single = state.activeSchools.size === 1 ? [...state.activeSchools][0] : null;
  byId("linesHint").textContent = toggle.checked && !single ? "Select exactly one school to show lines." : "";
  if (!toggle.checked || !single || !CAMPUS[single]) return;

  const color = SCHOOL_COLORS[single];
  const style = { renderer: state.linesRenderer, color, weight: 1, opacity: 0.3, interactive: false };
  for (const m of shown) L.polyline([CAMPUS[single], m.getLatLng()], style).addTo(state.lines);
  L.circleMarker(CAMPUS[single], {
    renderer: state.linesRenderer, radius: 6, color: "#fff", weight: 2,
    fillColor: color, fillOpacity: 1, interactive: false
  }).addTo(state.lines);
}

// Each school has a pill in the filter panel and a button in the bottom bar; both toggle the same selection.
function buildSchoolControls() {
  const grid = byId("schoolGrid");
  const bar = byId("schoolBar");
  const schools = Object.keys(SCHOOL_COLORS);
  const elements = {};
  grid.innerHTML = "";
  bar.innerHTML = "";

  function setActive(school, active, apply = true) {
    if (active) state.activeSchools.add(school);
    else state.activeSchools.delete(school);
    elements[school].pill.classList.toggle("off", !active);
    elements[school].btn.classList.toggle("off", !active);
    if (apply) applyFilters();
  }

  state.setAllSchools = (active) => {
    for (const school of schools) setActive(school, active, false);
    applyFilters();
  };

  for (const school of schools) {
    const color = SCHOOL_COLORS[school];

    const pill = document.createElement("div");
    pill.className = "school-pill";
    pill.innerHTML = `<span class="dot" style="background:${color}"></span>${school}`;

    const btn = document.createElement("button");
    btn.className = "school-btn";
    btn.style.background = color;
    btn.textContent = school;

    pill.classList.add("off");
    btn.classList.add("off");

    elements[school] = { pill, btn };
    const toggle = () => setActive(school, !state.activeSchools.has(school));
    pill.addEventListener("click", toggle);
    btn.addEventListener("click", toggle);

    grid.appendChild(pill);
    bar.appendChild(btn);
  }

  for (const [label, active] of [["Select all", true], ["Clear all", false]]) {
    const action = document.createElement("button");
    action.className = "link-btn";
    action.textContent = label;
    action.addEventListener("click", () => state.setAllSchools(active));
    bar.appendChild(action);
  }
}

function buildYearChips(data) {
  const row = byId("yearRow");
  const years = Array.from(new Set(data.years.flat())).sort((a, b) => a - b);
  state.activeYears = new Set(years);
  const chips = years.map((year) => {
    const chip = makeChip(formatSeason(year), year, () => state.activeYears, applyFilters);
    row.appendChild(chip);
    return chip;
  });

  state.setAllYears = (active) => {
    state.activeYears = new Set(active ? years : []);
    chips.forEach((chip) => chip.classList.toggle("off", !active));
    applyFilters();
  };
}

// Region chips show how many people each region has under the other filters; toggling one filters the map to it.
function buildRegionChips(data) {
  const row = byId("regionRow");
  const regions = data.region ? (data.region_order || Array.from(new Set(data.region))) : [];
  byId("regionSection").style.display = regions.length ? "" : "none";
  state.activeRegions = regions.length ? new Set(regions) : null;

  for (const region of regions) {
    const chip = makeChip(region, region, () => state.activeRegions, applyFilters);
    row.appendChild(chip);
    state.regionChips.set(region, chip);
  }

  state.setAllRegions = (active) => {
    if (!state.activeRegions) return;
    state.activeRegions = new Set(active ? regions : []);
    state.regionChips.forEach((chip) => chip.classList.toggle("off", !active));
    applyFilters();
  };
}

function updateRegionCounts(counts) {
  state.regionChips.forEach((chip, region) => {
    chip.textContent = `${region} \u00b7 ${(counts[region] || 0).toLocaleString()}`;
  });
}

function buildSportList(data) {
  const list = byId("sportList");
  const sports = Array.from(new Set(data.sport)).sort();
  state.activeSports = new Set(sports);
  const boxes = sports.map((sport) => {
    const row = document.createElement("label");
    row.className = "sport-row";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = true;
    input.addEventListener("change", () => {
      if (input.checked) state.activeSports.add(sport);
      else state.activeSports.delete(sport);
      applyFilters();
    });
    row.append(input, sport);
    list.appendChild(row);
    return input;
  });

  state.setAllSports = (active) => {
    state.activeSports = new Set(active ? sports : []);
    boxes.forEach((box) => (box.checked = active));
    applyFilters();
  };
}

// Name search: accent- and case-insensitive, every typed word must match; picking a result jumps to that person.
function buildSearch() {
  const input = byId("searchInput");
  const results = byId("searchResults");
  const norm = (s) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const index = state.markers.map((m) => ({ m, key: norm(m._name) }));

  function goTo(m) {
    if (!state.map.hasLayer(m)) m.addTo(state.map);
    state.map.setView(m.getLatLng(), Math.max(state.map.getZoom(), 8), { animate: false });
    m.openPopup();
    input.value = m._name;
    results.innerHTML = "";
  }

  function render() {
    results.innerHTML = "";
    const q = norm(input.value.trim());
    if (q.length < 2) return;
    const terms = q.split(/\s+/);
    const hits = index.filter((e) => terms.every((t) => e.key.includes(t))).slice(0, 8);
    for (const { m } of hits) {
      const row = document.createElement("div");
      row.className = "search-hit";
      row.textContent = m._name;
      const detail = document.createElement("span");
      detail.textContent = [m._school, m._hometown].filter(Boolean).join(" \u00b7 ");
      row.appendChild(detail);
      row.addEventListener("click", () => goTo(m));
      results.appendChild(row);
    }
    if (hits.length === 0) {
      const none = document.createElement("div");
      none.className = "search-hit none";
      none.textContent = "No matches";
      results.appendChild(none);
    }
  }

  input.addEventListener("input", render);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const first = results.querySelector(".search-hit:not(.none)");
      if (first) first.click();
    } else if (e.key === "Escape") {
      results.innerHTML = "";
    }
  });
  document.addEventListener("click", (e) => {
    if (!byId("search").contains(e.target)) results.innerHTML = "";
  });
}

// The Select all / Clear all buttons are #selectAll<Kind> and #clearAll<Kind>, backed by state.setAll<Kind>.
function wireActions() {
  byId("filterToggle").addEventListener("click", () => byId("filterPanel").classList.toggle("open"));
  byId("linesToggle").addEventListener("change", applyFilters);

  for (const kind of ["Schools", "Regions", "Years", "Sports"]) {
    byId(`selectAll${kind}`).addEventListener("click", () => state[`setAll${kind}`](true));
    byId(`clearAll${kind}`).addEventListener("click", () => state[`setAll${kind}`](false));
  }
}

async function main() {
  const data = await loadData();
  state.map = buildMap();
  state.markers = buildMarkers(state.map, data);

  buildSchoolControls();
  buildYearChips(data);
  buildRegionChips(data);
  buildSportList(data);
  buildSearch();
  wireActions();
  applyFilters();

  byId("loading").remove();
}

main().catch((err) => {
  byId("loading").textContent = "Failed to load data: " + err.message;
  console.error(err);
});
