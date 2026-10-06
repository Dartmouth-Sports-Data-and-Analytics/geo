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
const NO_TEAM_FILL = "#d1d5db";
const MAP_W = 975;
const MAP_H = 610;
const TOP_SPORTS = 8;
const RANK_COUNT = 5;

const ui = { years: new Set(), sports: new Set(), regions: null };
let data = null;
let features = [];
let agg = {};
const cards = {};

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

// d3 paints a wrongly wound outline over the whole map, so refuse to draw a file that pipeline/prepare_states.py hasn't fixed.
function checkWinding(list) {
  const bad = list.filter((f) => d3.geoArea(f) > 2 * Math.PI).map((f) => f.properties.name);
  if (bad.length) throw new Error(`us-states.json has wrongly wound outlines (${bad.join(", ")}); run pipeline/prepare_states.py`);
}

// Per school and state: unique players (and per-sport counts) passing the season/sport filters; non-US players are tallied apart.
function aggregate() {
  const out = {};
  for (const school of Object.keys(SCHOOL_COLORS)) {
    out[school] = { states: {}, total: 0, other: 0, max: 0, otherSeen: new Set(), hasTeam: false };
  }
  const n = data.sport.length;
  for (let i = 0; i < n; i++) {
    if (!ui.sports.has(data.sport[i])) continue;
    if (!data.years[i].some((y) => ui.years.has(y))) continue;
    const a = out[data.school[i]];
    if (!a) continue;
    a.hasTeam = true;
    if (ui.regions && !ui.regions.has(data.region[i])) continue;

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
    const list = Object.entries(a.states)
      .map(([code, s]) => ({ code, count: s.count }))
      .sort((x, y) => y.count - x.count || x.code.localeCompare(y.code));
    list.forEach((r) => {
      r.rank = 1 + list.filter((o) => o.count > r.count).length;
      a.states[r.code].rank = r.rank;
    });
    a.ranked = list;
  }
  return out;
}

// Nothing is selected until at least one season and one sport are chosen.
function noSelection() {
  return ui.years.size === 0 || ui.sports.size === 0;
}

function update() {
  agg = aggregate();
  const empty = noSelection();
  byId("emptyHint").hidden = !empty;

  const inferred = empty ? [] : INFERRED_SEASONS.filter((s) => ui.sports.has(s.sport) && ui.years.has(s.year));
  const note = byId("inferredNote");
  note.hidden = inferred.length === 0;
  note.textContent = inferred.length
    ? `Includes inferred rosters: ${inferred.map((s) => `${s.school} ${s.sport} ${formatSeason(s.year)}`).join("; ")}. ` +
      "The team page is blank on the school's site, so the roster was rebuilt from the previous and next seasons and may miss a few athletes."
    : "";

  for (const [school, card] of Object.entries(cards)) {
    const a = agg[school];
    const color = SCHOOL_COLORS[school];

    const noTeam = !empty && !a.hasTeam;
    card.el.classList.toggle("no-team", noTeam);
    card.el.classList.toggle("empty", empty);
    card.paths.attr("fill", (f) => {
      if (noTeam) return NO_TEAM_FILL;
      const st = a.states[STATE_CODES[f.properties.name]];
      return st && a.max ? rampColor(color, Math.sqrt(st.count / a.max)) : NO_DATA_FILL;
    });
    const shownTotals = !empty && !noTeam;
    card.total.textContent = empty ? "" : noTeam ? "No team" : `${a.total.toLocaleString()} US · ${a.other.toLocaleString()} Intl`;
    card.total.title = shownTotals ? `${a.total.toLocaleString()} players from US states, ${a.other.toLocaleString()} international` : "";
    card.legendMax.textContent = a.max.toLocaleString();
    card.rank.replaceChildren(...(empty || noTeam ? [] : a.ranked.slice(0, RANK_COUNT).map((r) => {
      const item = document.createElement("span");
      const num = document.createElement("em");
      num.textContent = `${r.rank}.`;
      const code = document.createElement("b");
      code.textContent = r.code;
      const count = document.createElement("i");
      count.textContent = r.count.toLocaleString();
      item.append(num, code, count);
      return item;
    })));
  }
  hideTip();
}

function showTip(event, school, feature) {
  if (noSelection()) return;
  const name = feature.properties.name;
  const a = agg[school];
  const st = a.states[STATE_CODES[name]];

  let html = `<div class="tip-title">${escapeHtml(name)} <span>&middot; ${school}</span></div>`;
  if (!a.hasTeam) {
    html += `<div class="tip-line">No team for this selection</div>`;
  } else if (!st) {
    html += `<div class="tip-line">No players</div>`;
  } else {
    const pct = a.total ? Math.round((100 * st.count) / a.total) : 0;
    html += `<div class="tip-line"><b>${st.count.toLocaleString()}</b> ${st.count === 1 ? "player" : "players"}` +
            ` &middot; ${pct}% of ${school}'s US players</div>`;
    html += `<div class="tip-line">Ranked #${st.rank} of ${a.ranked.length} ${a.ranked.length === 1 ? "state" : "states"}</div>`;
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

// A dropdown of checkboxes (with Select all / Clear all) that edits and returns a Set; used for seasons and sports.
const menuPanels = [];
function multiSelect({ btnId, panelId, listId, allId, noneId, items, format, plural, initial = items, presets = [] }) {
  const set = new Set(initial);
  const btn = byId(btnId);
  const panel = byId(panelId);
  const boxes = [];
  menuPanels.push(panel);

  const sameAs = (list) => list.length === set.size && list.every((item) => set.has(item));
  const label = () => {
    if (set.size === items.length) return `All ${plural}`;
    if (set.size === 0) return `Select ${plural}`;
    const preset = presets.find((p) => sameAs(p.pick(items)));
    if (preset) return preset.summary;
    if (set.size === 1) return format([...set][0]);
    return `${set.size} of ${items.length} ${plural}`;
  };
  const refresh = () => { btn.textContent = label(); update(); };

  for (const item of items) {
    const row = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = set.has(item);
    input.addEventListener("change", () => {
      if (input.checked) set.add(item);
      else set.delete(item);
      refresh();
    });
    row.appendChild(input);
    row.appendChild(document.createTextNode(format(item)));
    byId(listId).appendChild(row);
    boxes.push(input);
  }
  const choose = (list) => {
    set.clear();
    list.forEach((item) => set.add(item));
    boxes.forEach((b, i) => (b.checked = set.has(items[i])));
    refresh();
  };
  byId(allId).addEventListener("click", () => choose(items));
  byId(noneId).addEventListener("click", () => choose([]));
  for (const preset of presets) {
    const link = document.createElement("button");
    link.type = "button";
    link.className = "link-btn";
    link.textContent = preset.button;
    link.addEventListener("click", () => choose(preset.pick(items)));
    panel.querySelector(".menu-actions").appendChild(link);
  }
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = panel.hidden;
    menuPanels.forEach((p) => (p.hidden = true));
    panel.hidden = !open;
  });
  panel.addEventListener("click", (e) => e.stopPropagation());
  btn.textContent = label();
  return set;
}

function buildControls() {
  document.addEventListener("click", () => menuPanels.forEach((p) => (p.hidden = true)));

  const seasons = Array.from(new Set(data.years.flat())).sort((a, b) => a - b);
  ui.years = multiSelect({
    btnId: "yearBtn", panelId: "yearPanel", listId: "yearList", allId: "yearsAll", noneId: "yearsNone",
    items: seasons, format: formatSeason, plural: "seasons", initial: latestSeasons(seasons, DEFAULT_SEASONS),
    presets: [5].map((n) => ({ button: `Latest ${n}`, summary: `Latest ${n} seasons`, pick: (items) => latestSeasons(items, n) }))
  });
  ui.sports = multiSelect({
    btnId: "sportBtn", panelId: "sportPanel", listId: "sportList", allId: "sportsAll", noneId: "sportsNone",
    items: Array.from(new Set(data.sport)).sort(), format: (s) => s, plural: "sports"
  });

  // Regions: toggle chips (the US regions plus International / Other).
  const row = byId("regionRow");
  if (!data.region) { row.style.display = "none"; return; }
  const regions = data.region_order || Array.from(new Set(data.region));
  ui.regions = new Set(regions);
  const chips = regions.map((region) => {
    const chip = makeChip(region, region, () => ui.regions, update);
    byId("regionChips").appendChild(chip);
    return chip;
  });
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

    const rank = document.createElement("div");
    rank.className = "rank";
    el.appendChild(rank);

    const bar = `linear-gradient(to right, ${rampColor(color, 0)}, ${rampColor(color, 0.75)}, ${rampColor(color, 1)})`;
    const legend = document.createElement("div");
    legend.className = "legend";
    legend.innerHTML = `<span>1</span><div class="legend-bar" style="background:${bar}"></div><span class="legend-max"></span>`;
    el.appendChild(legend);
    grid.appendChild(el);

    cards[school] = { el, paths, rank, total: el.querySelector(".card-total"), legendMax: el.querySelector(".legend-max") };
  }
}

async function main() {
  const [dataRes, geoRes] = await Promise.all([fetch("data.json?v=b28f8926"), fetch("us-states.json?v=6ea79f14")]);
  if (!dataRes.ok) throw new Error("Could not load data.json");
  if (!geoRes.ok) throw new Error("Could not load us-states.json (run pipeline/prepare_states.py)");
  data = await dataRes.json();
  const geo = await geoRes.json();
  if (!data.state) throw new Error("data.json has no state field; rebuild it with build_data_json.py");

  features = geo.features.filter((f) => STATE_CODES[f.properties.name]);
  checkWinding(features);
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
