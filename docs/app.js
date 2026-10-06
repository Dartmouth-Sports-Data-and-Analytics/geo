const SCHOOL_COLORS = {
  Brown: "#4E3629",
  Columbia: "#9BCBEB",
  Cornell: "#B31B1B",
  Dartmouth: "#00693E",
  Harvard: "#A41034",
  Penn: "#011F5B",
  Princeton: "#FF671F",
  Yale: "#00356B"
};

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

// Heat map = US state shading by player count; outline style for the states.
const STATE_STYLE = { color: "#4b5563", weight: 1.2, opacity: 0.85 };

const STATE_CODES = {
  "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
  "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA",
  "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
  "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA",
  "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
  "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
  "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
  "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
  "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
  "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY"
};

function mixColor(a, b, t) {
  const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [x, y] = [rgb(a), rgb(b)];
  return `rgb(${x.map((v, i) => Math.round(v + (y[i] - v) * t)).join(",")})`;
}

// Light tint -> the school's color (at 75% of the scale) -> a darker shade, so busy states stand out.
function rampColor(hex, t) {
  if (t <= 0.75) return mixColor(hex, "#ffffff", 0.85 * (1 - t / 0.75));
  return mixColor(hex, "#000000", 0.3 * (t - 0.75) / 0.25);
}

const state = {
  data: null,
  map: null,
  markers: [],
  activeSchools: new Set(Object.keys(SCHOOL_COLORS)),
  activeYears: null,
  activeSports: null,
  activeRegions: null,
  regionChips: null,
  viewMode: "dots",
  statesLayer: null,
  stateLayers: {},
  legend: null,
  schoolsBeforeHeat: null
};

function byId(id) { return document.getElementById(id); }

async function loadData() {
  const res = await fetch("data.json?v=20");
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

  map.createPane("statesPane").style.zIndex = 360;
  map.createPane("linesPane").style.zIndex = 380;
  map.getPane("linesPane").style.pointerEvents = "none";
  state.linesRenderer = L.canvas({ pane: "linesPane", padding: 0.5 });
  state.lines = L.layerGroup().addTo(map);

  return map;
}

// One marker per person (shared `person` ID); popup lists each sport's seasons.
function buildMarkers(map, data) {
  const renderer = L.canvas({ padding: 0.5 });
  const n = data.lat.length;
  const people = new Map();

  for (let i = 0; i < n; i++) {
    const id = data.person ? data.person[i] : i;
    if (!people.has(id)) {
      people.set(id, {
        lat: data.lat[i], lng: data.lng[i],
        name: data.name[i], school: data.school[i], hometown: data.hometown[i],
        region: data.region ? data.region[i] : null,
        state: data.state ? data.state[i] : null,
        entries: []
      });
    }
    people.get(id).entries.push({ sport: data.sport[i], years: data.years[i] });
  }

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
      const key = e.sport;
      if (!bySport.has(key)) bySport.set(key, new Set());
      e.years.forEach((y) => bySport.get(key).add(y));
    }
    p.entries = [...bySport].map(([sport, ys]) => ({
      sport, years: [...ys].sort((a, b) => a - b)
    }));
    p.entries.sort((a, b) => a.sport.localeCompare(b.sport));
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

    marker._school = p.school;
    marker._region = p.region;
    marker._state = p.state;
    marker._entries = p.entries;
    marker._name = p.name || "Unknown";
    marker._hometown = p.hometown || "";

    const sportLines = p.entries.map((e) =>
      `<div class="popup-line">${escapeHtml(e.sport)} &middot; ${formatYearRanges(e.years)}</div>`
    ).join("");
    marker.bindPopup(
      `<div class="popup-name">${escapeHtml(p.name || "Unknown")}</div>` +
      `<div class="popup-line">${escapeHtml(p.school)}</div>` +
      sportLines +
      `<div class="popup-line">${escapeHtml(p.hometown || "")}</div>`
    );

    marker.addTo(map);
    markers.push(marker);
  }

  return markers;
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function formatSeason(year) {
  const secondYear = (year + 1) % 100;
  return `${year}-${String(secondYear).padStart(2, "0")}`;
}

function formatYearRanges(years) {
  if (!years || years.length === 0) return "";
  const sorted = Array.from(new Set(years)).sort((a, b) => a - b);
  return sorted.map(formatSeason).join(", ");
}

// Hidden markers are removed from the map (not made transparent) so they can't be clicked.
function applyFilters() {
  const { activeSchools, activeSports, activeYears, activeRegions, map } = state;
  let visible = 0;
  const shown = [];
  const regionCounts = {};

  for (const m of state.markers) {
    const base = activeSchools.has(m._school) &&
                 m._entries.some((e) =>
                   activeSports.has(e.sport) && e.years.some((y) => activeYears.has(y)));
    if (base && m._region) regionCounts[m._region] = (regionCounts[m._region] || 0) + 1;
    const show = base && (!activeRegions || activeRegions.has(m._region));
    if (show && state.viewMode === "dots") {
      if (!map.hasLayer(m)) m.addTo(map);
    } else if (map.hasLayer(m)) {
      m.closePopup();
      map.removeLayer(m);
    }
    if (show) {
      visible++;
      shown.push(m);
    }
  }

  byId("count").textContent = visible.toLocaleString();
  updateRegionCounts(regionCounts);
  updateHeat(shown);
  updateLines(shown);
}

// Shades each US state by how many people (from the filtered set) come from it; non-US hometowns aren't shown.
function updateHeat(shown) {
  if (!state.statesLayer) return;
  const heat = state.viewMode === "heat";
  if (!heat) {
    if (state.legend && state.legend._map) state.legend.remove();
    return;
  }

  const counts = {};
  for (const m of shown) if (m._state) counts[m._state] = (counts[m._state] || 0) + 1;
  const max = Math.max(0, ...Object.values(counts));
  const schools = new Set(shown.map((m) => m._school));
  const color = schools.size === 1 ? SCHOOL_COLORS[[...schools][0]] : "#374151";

  for (const [name, layer] of Object.entries(state.stateLayers)) {
    const n = counts[STATE_CODES[name]] || 0;
    layer.setStyle(n === 0
      ? { ...STATE_STYLE, fillColor: "#e5e7eb", fillOpacity: 0.25 }
      : { ...STATE_STYLE, fillColor: rampColor(color, Math.sqrt(n / max)), fillOpacity: 0.9 });
    layer.setTooltipContent(`${name}: ${n.toLocaleString()} ${n === 1 ? "player" : "players"}`);
  }
  updateLegend(color, max);
}

function updateLegend(color, max) {
  if (!state.legend) return;
  if (!state.legend._map) state.legend.addTo(state.map);
  const bar = `linear-gradient(to right, ${rampColor(color, 0)}, ${rampColor(color, 0.75)}, ${rampColor(color, 1)})`;
  state.legend.getContainer().innerHTML =
    `<div class="legend-title">Players from each state</div>` +
    `<div class="legend-bar" style="background:${bar}"></div>` +
    `<div class="legend-scale"><span>1</span><span>${max.toLocaleString()}</span></div>`;
}

// Entering heat mode keeps one school (the first selected) and remembers the rest; leaving restores them.
function setViewMode(mode) {
  const wasHeat = state.viewMode === "heat";
  state.viewMode = mode;
  const heat = mode === "heat";

  const btn = byId("viewToggle");
  btn.textContent = heat ? "Dot map" : "Heat map";
  btn.classList.toggle("active", heat);
  byId("schoolActions").style.display = heat ? "none" : "";
  byId("schoolHint").textContent = heat ? "The heat map shows one school at a time." : "";
  state.map.closePopup();
  syncStates();

  const all = Object.keys(SCHOOL_COLORS);
  if (heat && !wasHeat) {
    state.schoolsBeforeHeat = [...state.activeSchools];
    state.setSchools([all.find((s) => state.activeSchools.has(s)) || all[0]]);
  } else if (!heat && wasHeat) {
    state.setSchools(state.schoolsBeforeHeat && state.schoolsBeforeHeat.length ? state.schoolsBeforeHeat : all);
  } else {
    applyFilters();
  }
}

function syncStates() {
  if (!state.statesLayer || !state.map) return;
  const wanted = state.viewMode === "heat";
  if (wanted && !state.map.hasLayer(state.statesLayer)) state.statesLayer.addTo(state.map);
  if (!wanted && state.map.hasLayer(state.statesLayer)) state.map.removeLayer(state.statesLayer);
}

// Loads docs/us-states.json; the heat map button only appears once it and per-person state data are available.
async function loadStates(map, data) {
  try {
    if (!data.state) return;
    const res = await fetch("us-states.json?v=24");
    if (!res.ok) return;
    state.statesLayer = L.geoJSON(await res.json(), {
      pane: "statesPane",
      renderer: L.canvas({ pane: "statesPane" }),
      style: { ...STATE_STYLE, fillColor: "#e5e7eb", fillOpacity: 0.25 },
      onEachFeature: (feature, layer) => {
        layer.bindTooltip("", { sticky: true });
        state.stateLayers[feature.properties.name] = layer;
      }
    });
    state.legend = L.control({ position: "bottomleft" });
    state.legend.onAdd = () => L.DomUtil.create("div", "legend");
    byId("viewToggle").style.display = "";
    syncStates();
  } catch (err) {
    console.warn("State shading unavailable:", err);
  }
}

// Lines from campus to every shown hometown; only drawn when the toggle is on and exactly one school is active.
function updateLines(shown) {
  const toggle = byId("linesToggle");
  if (!toggle || !state.lines) return;
  state.lines.clearLayers();
  const single = state.activeSchools.size === 1 ? [...state.activeSchools][0] : null;
  byId("linesHint").textContent = toggle.checked && !single ? "Select exactly one school to show lines." : "";
  if (state.viewMode === "heat" || !toggle.checked || !single || !CAMPUS[single]) return;

  const color = SCHOOL_COLORS[single];
  const style = { renderer: state.linesRenderer, color, weight: 1, opacity: 0.3, interactive: false };
  for (const m of shown) L.polyline([CAMPUS[single], m.getLatLng()], style).addTo(state.lines);
  L.circleMarker(CAMPUS[single], {
    renderer: state.linesRenderer, radius: 6, color: "#fff", weight: 2,
    fillColor: color, fillOpacity: 1, interactive: false
  }).addTo(state.lines);
}

function buildSchoolControls(data) {
  const grid = byId("schoolGrid");
  const bar = byId("schoolBar");
  const schools = Object.keys(SCHOOL_COLORS);
  grid.innerHTML = "";
  bar.innerHTML = "";

  const elements = {};

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

  state.setSchools = (selected) => {
    for (const school of schools) setActive(school, selected.includes(school), false);
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

    elements[school] = { pill, btn };

    // The heat map shows one school at a time, so a click there selects only that school.
    const toggle = () => (state.viewMode === "heat"
      ? state.setSchools([school])
      : setActive(school, !state.activeSchools.has(school)));
    pill.addEventListener("click", toggle);
    btn.addEventListener("click", toggle);

    grid.appendChild(pill);
    bar.appendChild(btn);
  }
}

function buildYearChips(data) {
  const row = byId("yearRow");
  const years = Array.from(new Set(data.years.flat())).sort((a, b) => a - b);
  state.activeYears = new Set(years);
  row.innerHTML = "";

  const chips = [];
  state.setAllYears = (active) => {
    state.activeYears = new Set(active ? years : []);
    chips.forEach((chip) => chip.classList.toggle("off", !active));
    applyFilters();
  };

  for (const year of years) {
    const chip = document.createElement("div");
    chip.className = "year-chip";
    chip.textContent = formatSeason(year);
    chip.addEventListener("click", () => {
      if (state.activeYears.has(year)) {
        state.activeYears.delete(year);
        chip.classList.add("off");
      } else {
        state.activeYears.add(year);
        chip.classList.remove("off");
      }
      applyFilters();
    });
    row.appendChild(chip);
    chips.push(chip);
  }
}

// Region chips show how many people each region has under the other filters; toggling one filters the map to it.
function buildRegionChips(data) {
  const row = byId("regionRow");
  const regions = data.region ? (data.region_order || Array.from(new Set(data.region))) : [];
  byId("regionSection").style.display = regions.length ? "" : "none";
  state.activeRegions = regions.length ? new Set(regions) : null;
  state.regionChips = new Map();
  row.innerHTML = "";

  for (const region of regions) {
    const chip = document.createElement("div");
    chip.className = "year-chip";
    chip.textContent = region;
    chip.addEventListener("click", () => {
      if (state.activeRegions.has(region)) {
        state.activeRegions.delete(region);
        chip.classList.add("off");
      } else {
        state.activeRegions.add(region);
        chip.classList.remove("off");
      }
      applyFilters();
    });
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
  if (!state.regionChips) return;
  state.regionChips.forEach((chip, region) => {
    chip.textContent = `${region} \u00b7 ${(counts[region] || 0).toLocaleString()}`;
  });
}

function buildSportList(data) {
  const list = byId("sportList");
  const sports = Array.from(new Set(data.sport)).sort();
  state.activeSports = new Set(sports);
  list.innerHTML = "";
  for (const sport of sports) {
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
    row.appendChild(input);
    row.appendChild(document.createTextNode(sport));
    list.appendChild(row);
  }
}

// Name search: accent- and case-insensitive, every typed word must match; picking a result jumps to that person.
function buildSearch() {
  const input = byId("searchInput");
  const results = byId("searchResults");
  const norm = (s) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const index = state.markers.map((m) => ({ m, key: norm(m._name) }));

  function goTo(m) {
    if (state.viewMode !== "dots") setViewMode("dots");
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

function wireActions() {
  byId("filterToggle").addEventListener("click", () => {
    byId("filterPanel").classList.toggle("open");
  });

  byId("linesToggle").addEventListener("change", applyFilters);
  byId("viewToggle").addEventListener("click", () => setViewMode(state.viewMode === "dots" ? "heat" : "dots"));

  byId("selectAllSchools").addEventListener("click", () => state.setAllSchools(true));
  byId("clearAllSchools").addEventListener("click", () => state.setAllSchools(false));
  byId("selectAllRegions").addEventListener("click", () => state.setAllRegions(true));
  byId("clearAllRegions").addEventListener("click", () => state.setAllRegions(false));
  byId("selectAllYears").addEventListener("click", () => state.setAllYears(true));
  byId("clearAllYears").addEventListener("click", () => state.setAllYears(false));

  byId("selectAllSports").addEventListener("click", () => {
    state.activeSports = new Set(state.data.sport);
    document.querySelectorAll("#sportList input").forEach((cb) => (cb.checked = true));
    applyFilters();
  });

  byId("clearAllSports").addEventListener("click", () => {
    state.activeSports = new Set();
    document.querySelectorAll("#sportList input").forEach((cb) => (cb.checked = false));
    applyFilters();
  });
}

async function main() {
  const data = await loadData();
  state.data = data;

  const map = buildMap();
  state.map = map;
  state.markers = buildMarkers(map, data);

  buildSchoolControls(data);
  buildYearChips(data);
  buildRegionChips(data);
  buildSportList(data);
  buildSearch();
  byId("viewToggle").style.display = "none";
  loadStates(map, data);
  wireActions();
  applyFilters();

  byId("loading").remove();
}

main().catch((err) => {
  byId("loading").textContent = "Failed to load data: " + err.message;
  console.error(err);
});
