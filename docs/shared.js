// Constants and helpers shared by the roster map (app.js) and the heat maps (heatmaps.js).
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

// A toggle chip for one value in a selection; getSet returns the live Set, since Select all / Clear all replace it.
function makeChip(label, value, getSet, onChange) {
  const chip = document.createElement("div");
  chip.className = "chip";
  chip.textContent = label;
  chip.addEventListener("click", () => {
    const set = getSet();
    if (set.has(value)) set.delete(value);
    else set.add(value);
    chip.classList.toggle("off", !set.has(value));
    onChange();
  });
  return chip;
}
