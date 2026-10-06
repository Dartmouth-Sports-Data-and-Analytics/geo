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

const ui = { year: "all", sports: new Set(), regions: null };
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
    if (!ui.sports.has(data.sport[i])) continue;
    if (ui.regions && !ui.regions.has(data.region[i])) continue;
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

  for (const [school, card] of Object.entries(cards)) {
    const a = agg[school];
    const color = SCHOOL_COLORS[school];
    const max = a.max;

    card.paths.attr("fill", (f) => {
      const st = a.states[STATE_CODES[f.properties.name]];
      return st && max ? rampColor(color, Math.sqrt(st.count / max)) : NO_DATA_FILL;
    });
    card.total.textContent = `${a.total.toLocaleString()} US players` +
      (a.other ? ` · ${a.other.toLocaleString()} international` : "");
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

function sportButtonLabel(total) {
  const n = ui.sports.size;
  if (n === total) return "All sports";
  if (n === 0) return "No sports";
  if (n === 1) return [...ui.sports][0];
  return `${n} of ${total} sports`;
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

  // Sports: a dropdown of checkboxes, so any combination can be picked.
  const sports = Array.from(new Set(data.sport)).sort();
  ui.sports = new Set(sports);
  const btn = byId("sportBtn");
  const panel = byId("sportPanel");
  const list = byId("sportList");
  const boxes = [];
  const refresh = () => { btn.textContent = sportButtonLabel(sports.length); update(); };
  for (const sport of sports) {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = true;
    input.addEventListener("change", () => {
      if (input.checked) ui.sports.add(sport);
      else ui.sports.delete(sport);
      refresh();
    });
    label.appendChild(input);
    label.appendChild(document.createTextNode(sport));
    list.appendChild(label);
    boxes.push(input);
  }
  byId("sportsAll").addEventListener("click", () => { ui.sports = new Set(sports); boxes.forEach((b) => (b.checked = true)); refresh(); });
  byId("sportsNone").addEventListener("click", () => { ui.sports = new Set(); boxes.forEach((b) => (b.checked = false)); refresh(); });
  btn.addEventListener("click", (e) => { e.stopPropagation(); panel.hidden = !panel.hidden; });
  panel.addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", () => { panel.hidden = true; });

  // Regions: toggle chips (the US regions plus International / Other).
  const row = byId("regionRow");
  if (!data.region) { row.style.display = "none"; return; }
  const regions = data.region_order || Array.from(new Set(data.region));
  ui.regions = new Set(regions);
  const chips = [];
  for (const region of regions) {
    const chip = document.createElement("div");
    chip.className = "chip";
    chip.textContent = region;
    chip.addEventListener("click", () => {
      if (ui.regions.has(region)) ui.regions.delete(region);
      else ui.regions.add(region);
      chip.classList.toggle("off", !ui.regions.has(region));
      update();
    });
    byId("regionChips").appendChild(chip);
    chips.push(chip);
  }
  const setAll = (on) => { ui.regions = new Set(on ? regions : []); chips.forEach((c) => c.classList.toggle("off", !on)); update(); };
  byId("regionsAll").addEventListener("click", () => setAll(true));
  byId("regionsNone").addEventListener("click", () => setAll(false));
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
