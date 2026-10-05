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

const state = {
  data: null,
  map: null,
  markers: [],
  activeSchools: new Set(Object.keys(SCHOOL_COLORS)),
  activeYears: null,
  activeSports: null
};

function byId(id) { return document.getElementById(id); }

async function loadData() {
  const res = await fetch("data.json");
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
        entries: []
      });
    }
    people.get(id).entries.push({ sport: data.sport[i], years: data.years[i] });
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
    const marker = L.circleMarker([p.lat, p.lng], {
      renderer,
      radius: 7,
      color,
      fillColor: color,
      fillOpacity: 0.75,
      weight: 0,
      stroke: false
    });

    marker._school = p.school;
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
  const { activeSchools, activeSports, activeYears, map } = state;
  let visible = 0;

  for (const m of state.markers) {
    const show = activeSchools.has(m._school) &&
                 m._entries.some((e) =>
                   activeSports.has(e.sport) && e.years.some((y) => activeYears.has(y)));
    if (show) {
      if (!map.hasLayer(m)) m.addTo(map);
      visible++;
    } else if (map.hasLayer(m)) {
      m.closePopup();
      map.removeLayer(m);
    }
  }

  byId("count").textContent = visible.toLocaleString();
}

function buildSchoolControls(data) {
  const grid = byId("schoolGrid");
  const bar = byId("schoolBar");
  const schools = Object.keys(SCHOOL_COLORS);
  grid.innerHTML = "";
  bar.innerHTML = "";

  const elements = {};

  function setActive(school, active) {
    if (active) state.activeSchools.add(school);
    else state.activeSchools.delete(school);

    elements[school].pill.classList.toggle("off", !active);
    elements[school].btn.classList.toggle("off", !active);
    applyFilters();
  }

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

    const toggle = () => setActive(school, !state.activeSchools.has(school));
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
  }
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
