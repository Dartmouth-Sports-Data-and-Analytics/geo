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

const NO_DATA_FILL = "#eceff3";
const MAP_W = 975;
const MAP_H = 610;
const TOP_SPORTS = 8;

const ui = { year: "all", sport: "all", scale: "school" };
let data = null;
let features = [];
let agg = {};
const cards = {};

function byId(id) { return document.getElementById(id); }

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

// 2024 -> "2024-25"
function formatSeason(year) {
  return `${year}-${String((year + 1) % 100).padStart(2, "0")}`;
}

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

// The outline file's polygon winding is inconsistent, and d3 reads a wrongly wound ring as "everything except the state";
// any polygon covering more than half the globe is therefore reversed. Using spherical area also handles the antimeridian.
function fixWinding(geometry) {
  const flip = (poly) => (d3.geoArea({ type: "Polygon", coordinates: poly }) > 2 * Math.PI
    ? poly.map((ring) => ring.slice().reverse())
    : poly);
  if (geometry.type === "Polygon") return { ...geometry, coordinates: flip(geometry.coordinates) };
  if (geometry.type === "MultiPolygon") return { ...geometry, coordinates: geometry.coordinates.map(flip) };
  return geometry;
}

// Per school and state: unique players (and per-sport counts) passing the season/sport filters; non-US players are tallied apart.
function aggregate() {
  const out = {};
  for (const school of Object.keys(SCHOOL_COLORS)) {
    out[school] = { states: {}, total: 0, other: 0, max: 0, otherSeen: new Set() };
  }
  const n = data.sport.length;
  for (let i = 0; i < n; i++) {
    if (ui.sport !== "all" && data.sport[i] !== ui.sport) continue;
    if (ui.year !== "all" && !data.years[i].includes(ui.year)) continue;
    const a = out[data.school[i]];
    if (!a) continue;

    const id = data.person ? data.person[i] : i;
    const code = data.state[i];
    if (!code) {
      if (!a.otherSeen.has(id)) { a.otherSeen.add(id); a.other++; }
      continue;
    }
    const st = a.states[code] || (a.states[code] = { count: 0, ids: new Set(), sports: {} });
    st.sports[data.sport[i]] = (st.sports[data.sport[i]] || 0) + 1;
    if (!st.ids.has(id)) { st.ids.add(id); st.count++; a.total++; }
  }
  for (const a of Object.values(out)) {
    a.max = Math.max(0, ...Object.values(a.states).map((s) => s.count));
  }
  return out;
}

function update() {
  agg = aggregate();
  const sharedMax = Math.max(0, ...Object.values(agg).map((a) => a.max));

  for (const [school, card] of Object.entries(cards)) {
    const a = agg[school];
    const color = SCHOOL_COLORS[school];
    const max = ui.scale === "shared" ? sharedMax : a.max;

    card.paths.attr("fill", (f) => {
      const st = a.states[STATE_CODES[f.properties.name]];
      return st && max ? rampColor(color, Math.sqrt(st.count / max)) : NO_DATA_FILL;
    });
    card.total.textContent = `${a.total.toLocaleString()} US players` +
      (a.other ? ` · ${a.other.toLocaleString()} elsewhere` : "");
    card.legendMax.textContent = max.toLocaleString();
  }
  hideTip();
}

function showTip(event, school, feature) {
  const name = feature.properties.name;
  const a = agg[school];
  const st = a.states[STATE_CODES[name]];

  let html = `<div class="tip-title">${escapeHtml(name)} <span>&middot; ${school}</span></div>`;
  if (!st) {
    html += `<div class="tip-line">No players</div>`;
  } else {
    const pct = a.total ? Math.round((100 * st.count) / a.total) : 0;
    html += `<div class="tip-line"><b>${st.count.toLocaleString()}</b> ${st.count === 1 ? "player" : "players"}` +
            ` &middot; ${pct}% of ${school}'s US players</div>`;
    const sports = Object.entries(st.sports).sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0]));
    html += "<table>" + sports.slice(0, TOP_SPORTS)
      .map(([s, c]) => `<tr><td>${escapeHtml(s)}</td><td>${c.toLocaleString()}</td></tr>`).join("") + "</table>";
    if (sports.length > TOP_SPORTS) html += `<div class="tip-more">+${sports.length - TOP_SPORTS} more sports</div>`;
  }

  const tip = byId("tip");
  tip.innerHTML = html;
  tip.style.display = "block";
  const pad = 14;
  let x = event.clientX + pad;
  let y = event.clientY + pad;
  if (x + tip.offsetWidth > window.innerWidth - 8) x = event.clientX - tip.offsetWidth - pad;
  if (y + tip.offsetHeight > window.innerHeight - 8) y = window.innerHeight - tip.offsetHeight - 8;
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${Math.max(8, y)}px`;
}

function hideTip() {
  byId("tip").style.display = "none";
}

function buildControls() {
  const years = Array.from(new Set(data.years.flat())).sort((a, b) => a - b);
  const yearSel = byId("yearSelect");
  yearSel.innerHTML = `<option value="all">All seasons</option>` +
    years.map((y) => `<option value="${y}">${formatSeason(y)}</option>`).join("");
  yearSel.addEventListener("change", () => {
    ui.year = yearSel.value === "all" ? "all" : Number(yearSel.value);
    update();
  });

  const sports = Array.from(new Set(data.sport)).sort();
  const sportSel = byId("sportSelect");
  sportSel.innerHTML = `<option value="all">All sports</option>` +
    sports.map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
  sportSel.addEventListener("change", () => {
    ui.sport = sportSel.value;
    update();
  });

  byId("scaleToggle").querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      ui.scale = btn.dataset.scale;
      byId("scaleToggle").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === btn));
      update();
    });
  });
}

// One card per school; Alaska and Hawaii are tucked below the lower 48 by the Albers USA projection.
function buildCards() {
  const projection = d3.geoAlbersUsa().scale(1300).translate([MAP_W / 2, MAP_H / 2]);
  const path = d3.geoPath(projection);
  const grid = byId("grid");

  for (const [school, color] of Object.entries(SCHOOL_COLORS)) {
    const el = document.createElement("section");
    el.className = "card";
    el.innerHTML =
      `<div class="card-head"><h2><span class="dot" style="background:${color}"></span>${school}</h2><span class="card-total"></span></div>`;

    const svg = d3.select(el).append("svg")
      .attr("viewBox", `0 0 ${MAP_W} ${MAP_H}`)
      .attr("role", "img")
      .attr("aria-label", `${school} players by US state`);
    const paths = svg.selectAll("path").data(features).join("path")
      .attr("class", "st")
      .attr("d", path)
      .attr("fill", NO_DATA_FILL)
      .on("mousemove", (event, f) => showTip(event, school, f))
      .on("mouseleave", hideTip)
      .on("click", (event, f) => { event.stopPropagation(); showTip(event, school, f); });

    const bar = `linear-gradient(to right, ${rampColor(color, 0)}, ${rampColor(color, 0.75)}, ${rampColor(color, 1)})`;
    const legend = document.createElement("div");
    legend.className = "legend";
    legend.innerHTML = `<span>1</span><div class="legend-bar" style="background:${bar}"></div><span class="legend-max"></span>`;
    el.appendChild(legend);
    grid.appendChild(el);

    cards[school] = { el, paths, total: el.querySelector(".card-total"), legendMax: el.querySelector(".legend-max") };
  }
}

async function main() {
  const [dataRes, geoRes] = await Promise.all([fetch("data.json?v=20"), fetch("us-states.json?v=1")]);
  if (!dataRes.ok) throw new Error("Could not load data.json");
  if (!geoRes.ok) throw new Error("Could not load us-states.json (see README: download it into docs/)");
  data = await dataRes.json();
  const geo = await geoRes.json();
  if (!data.state) throw new Error("data.json has no state field; rebuild it with build_data_json.py");

  features = geo.features
    .filter((f) => STATE_CODES[f.properties.name])
    .map((f) => ({ ...f, geometry: fixWinding(f.geometry) }));
  buildControls();
  buildCards();
  document.addEventListener("click", hideTip);
  update();
  byId("loading").remove();
}

main().catch((err) => {
  byId("loading").textContent = "Failed to load: " + err.message;
  console.error(err);
});
