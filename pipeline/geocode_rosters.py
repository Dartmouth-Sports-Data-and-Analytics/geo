"""geocode_rosters.py — adds coordinates to every school's rosters via Nominatim, caching each hometown.

Also holds the hometown parsing (state and country spellings) that build_data_json.py reuses to work out each athlete's state."""
import argparse
import glob
import json
import math
import os
import re

import pandas as pd

import config

GEO_DIR = config.GEO_DIR

# Set a real contact; Nominatim can block generic user agents.
NOMINATIM_USER_AGENT = "ivy_roster_map_geocoder (contact: sarah.m.lammert@dartmouth.edu)"

_geocode = None


def _get_geocode():
    """One shared, rate-limited geocoder for the whole run (1.1s gap, longer retry backoff, 10s timeout, to respect Nominatim's
    1 request/second policy). Built on first use so importing this module needs neither geopy nor the network."""
    global _geocode
    if _geocode is None:
        from geopy.extra.rate_limiter import RateLimiter
        from geopy.geocoders import Nominatim
        _geocode = RateLimiter(
            Nominatim(user_agent=NOMINATIM_USER_AGENT, timeout=10).geocode,
            min_delay_seconds=1.1,
            error_wait_seconds=10.0,
            max_retries=5,
            swallow_exceptions=False,  # errors raise (and are retried next run); None means a genuine no-match
        )
    return _geocode


def geo_path(school):
    return os.path.join(GEO_DIR, f"{school.lower()}_rosters_geo.csv")


# Standalone hometown -> (lat, lon) cache, so geo-rosters files aren't rescanned every run.
HOMETOWN_CACHE_PATH = config.HOMETOWN_CACHE_PATH
_HOMETOWN_CACHE_COLUMNS = ["hometown", "latitude", "longitude"]


def load_hometown_cache_file():
    if not os.path.exists(HOMETOWN_CACHE_PATH):
        return {}
    df = pd.read_csv(HOMETOWN_CACHE_PATH)
    df = df.dropna(subset=["hometown", "latitude", "longitude"])
    return dict(zip(df["hometown"], zip(df["latitude"], df["longitude"])))


def save_hometown_cache_file(hometown_cache):
    rows = [
        {"hometown": h, "latitude": lat, "longitude": lon}
        for h, (lat, lon) in sorted(hometown_cache.items())
        if lat is not None and lon is not None
    ]
    os.makedirs(os.path.dirname(HOMETOWN_CACHE_PATH), exist_ok=True)
    df = pd.DataFrame(rows, columns=_HOMETOWN_CACHE_COLUMNS)
    df.to_csv(HOMETOWN_CACHE_PATH, index=False)


# Trailing state/country spellings Nominatim doesn't recognize, matched case-insensitively without the final period.
_TRAILING_FIXES = {
    "wisc": "Wisconsin", "penn": "Pennsylvania", "vir": "Virginia", "ida": "Idaho", "ari": "Arizona",
    "wva": "West Virginia", "c.t": "Connecticut", "calf": "California",
    "aus": "Australia", "aust": "Australia", "n.z": "New Zealand", "u.k": "United Kingdom", "uk": "United Kingdom",
    "great britain": "United Kingdom", "ger": "Germany", "ire": "Ireland", "haw": "Hawaii",
}


# Splits a hometown into its comma-separated parts, dropping a parenthetical and anything after "/", "&" or "|" ("Paris / Lyon, France" -> Paris).
def hometown_parts(text):
    s = re.sub(r"\s*\([^)]*\)", "", text)
    s = re.split(r"\s*/\s*|\s+&\s+|\s*\|\s*", s)[0]
    parts = [p.strip() for p in s.split(",") if p.strip()]
    return _split_state_without_comma(parts[0]) if len(parts) == 1 else parts


# "Irvine Calif.", "Plano. TX" and "Orlando FL" have no comma before the state, so they would read as international. Only an
# abbreviation counts (it has a period, or is two capital letters), so "Kansas City" and "West Virginia" are left alone.
def _split_state_without_comma(text):
    m = re.match(r"^(?P<place>.+?)[.\s]+(?P<state>[A-Za-z][A-Za-z.]{1,7})$", text)
    if m:
        token = m.group("state")
        letters = re.sub(r"[^a-z]", "", token.lower())
        abbreviated = "." in token or (len(letters) == 2 and token.isupper())
        if abbreviated and letters in _STATE_LOOKUP and re.sub(r"[^a-z]", "", text.lower()) not in _STATE_LOOKUP:
            return [m.group("place").strip(), token]
    return [text]


# Letters-only lowercase spellings (full names, AP and USPS abbreviations, and spellings seen in the rosters) -> USPS code.
# Left out on purpose because a country uses them too: "Col." (Colombia), "Ken." (Kenya) and "Kent" (England); hand-fix those.
_STATE_NAMES = {
    "AL": "alabama ala al", "AK": "alaska ak", "AZ": "arizona ariz az ari", "AR": "arkansas ark ar",
    "CA": "california calif ca calf cal", "CO": "colorado colo co", "CT": "connecticut conn ct",
    "DE": "delaware del de", "DC": "districtofcolumbia dc", "FL": "florida fla fl flo", "GA": "georgia ga",
    "HI": "hawaii hi haw", "ID": "idaho ida id", "IL": "illinois ill il", "IN": "indiana ind in", "IA": "iowa ia",
    "KS": "kansas kan kans ks", "KY": "kentucky ky", "LA": "louisiana la", "ME": "maine me",
    "MD": "maryland md mary", "MA": "massachusetts mass ma", "MI": "michigan mich mi", "MN": "minnesota minn mn min",
    "MS": "mississippi miss ms", "MO": "missouri mo", "MT": "montana mont mt", "NE": "nebraska neb nebr ne",
    "NV": "nevada nev nv", "NH": "newhampshire nh", "NJ": "newjersey nj", "NM": "newmexico nm",
    "NY": "newyork ny", "NC": "northcarolina nc", "ND": "northdakota nd", "OH": "ohio oh ohi",
    "OK": "oklahoma okla ok", "OR": "oregon ore or", "PA": "pennsylvania penn pa", "RI": "rhodeisland ri",
    "SC": "southcarolina sc", "SD": "southdakota sd", "TN": "tennessee tenn tn", "TX": "texas tex tx",
    "UT": "utah ut", "VT": "vermont vt", "VA": "virginia vir va", "WA": "washington wash wa",
    "WV": "westvirginia wva wv", "WI": "wisconsin wis wisc wi", "WY": "wyoming wyo wy",
}
_STATE_LOOKUP = {name: code for code, names in _STATE_NAMES.items() for name in names.split()}
_USA_TOKENS = ("usa", "us", "unitedstates", "unitedstatesofamerica")

# USPS code -> full name. A US place is searched for with the full state name: Nominatim reads "Bethlehem, Pa." as Pará (Brazil),
# "Saint Charles, IL" as Israel and "Los Gatos, C.A." as somewhere in Asia.
STATE_FULL_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


# State code from the last comma-separated part of the hometown (after hand fixes); None means not a recognizable US state.
def us_state(hometown, fixes):
    parts = hometown_parts(fixes.get(hometown, hometown))
    while parts and re.sub(r"[^a-z]", "", parts[-1].lower()) in _USA_TOKENS:
        parts.pop()
    if len(parts) < 2:
        return None
    return _STATE_LOOKUP.get(re.sub(r"[^a-z]", "", parts[-1].lower()))


# ───────────────────────────── is a dot where its hometown says? ─────────────────────────────
# Uses the state outlines in docs/us-states.json (the file the heat maps draw). Without that file every check below is skipped.
# The geocoder applies this to each answer before caching it, and `checks.py places` applies it to the published data.
PLACE_KM_LIMIT = 100   # the outlines are coarse, so a town on a coast or border can sit a few dozen km off
_outlines = None
_boxes = None


def _state_rings(geometry):
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    return [polygon[0] for polygon in polygons]


def state_outlines():
    """{USPS code: [rings]} from docs/us-states.json; {} if the file is missing."""
    global _outlines, _boxes
    if _outlines is None:
        _outlines, _boxes = {}, {}
        path = os.path.join(config.DOCS_DIR, "us-states.json")
        if os.path.exists(path):
            with open(path) as f:
                geo = json.load(f)
            code_of = {name: code for code, name in STATE_FULL_NAMES.items()}
            for feature in geo["features"]:
                code = code_of.get(feature["properties"]["name"])
                if code:
                    rings = _state_rings(feature["geometry"])
                    _outlines[code] = rings
                    _boxes[code] = (min(x for r in rings for x, _ in r), max(x for r in rings for x, _ in r),
                                    min(y for r in rings for _, y in r), max(y for r in rings for _, y in r))
    return _outlines


def _ring_contains(ring, lng, lat):
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat) and lng < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def state_at(lat, lng):
    """The state whose outline holds this point, or None."""
    outlines = state_outlines()
    for code, (x0, x1, y0, y1) in _boxes.items():
        if x0 <= lng <= x1 and y0 <= lat <= y1 and any(_ring_contains(r, lng, lat) for r in outlines[code]):
            return code
    return None


def km_from_state(code, lat, lng):
    """Distance from a point to the nearest edge of a state's outline (0 if inside)."""
    best, scale = float("inf"), math.cos(math.radians(lat)) * 111.3
    for ring in state_outlines()[code]:
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            ax, ay, bx, by = (x1 - lng) * scale, (y1 - lat) * 111.3, (x2 - lng) * scale, (y2 - lat) * 111.3
            dx, dy = bx - ax, by - ay
            length = dx * dx + dy * dy
            t = 0 if length == 0 else max(0, min(1, -(ax * dx + ay * dy) / length))
            best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return 0 if state_at(lat, lng) == code else best


def is_country_only_us(text):
    """A hometown that is just "USA" / "US" / "United States": the country is known and the town is not."""
    return re.sub(r"[^a-z]", "", text.lower()) in _USA_TOKENS


def misplaced_reason(hometown, lat, lng, fixes):
    """Why this answer cannot be right for this hometown, or None if it looks fine (or cannot be checked)."""
    if not state_outlines():
        return None
    claimed, landed = us_state(hometown, fixes), state_at(lat, lng)
    if is_country_only_us(fixes.get(hometown, hometown)):
        # No state to contradict: the geocoder puts "USA" at the middle of the country, which is where the dot should be.
        return None if landed else "the hometown says USA but the geocoder put it outside the US"
    if claimed:
        if landed == claimed:
            return None
        km = km_from_state(claimed, lat, lng)
        if km > PLACE_KM_LIMIT:
            where = f" (in {STATE_FULL_NAMES[landed]})" if landed else ""
            return f"the hometown says {STATE_FULL_NAMES[claimed]} but the geocoder put it {round(km):,} km away{where}"
    elif landed:
        return f"the hometown names no US state but the geocoder put it in {STATE_FULL_NAMES[landed]}"
    return None


def misplaced_hometowns(d):
    """Rows [hometown, athletes, labelled_as, dot_is_in, km, lat, lng, guess] for every hometown in a data.json dict whose dot
    disagrees with its label; None if the outline file is missing."""
    outlines = state_outlines()
    if not outlines:
        return None
    towns = {}
    for i, hometown in enumerate(d["hometown"]):
        entry = towns.setdefault(hometown, {"state": d["state"][i], "lat": d["lat"][i], "lng": d["lng"][i], "athletes": 0})
        entry["athletes"] += 1
    rows = []
    for hometown, t in towns.items():
        landed = state_at(t["lat"], t["lng"])
        if t["state"] and t["state"] in outlines:
            if landed == t["state"]:
                continue
            km = km_from_state(t["state"], t["lat"], t["lng"])
            if km > PLACE_KM_LIMIT:
                rows.append([hometown, t["athletes"], t["state"], landed or "outside the US", round(km), t["lat"], t["lng"], ""])
        elif not t["state"] and is_country_only_us(hometown):
            if not landed:
                rows.append([hometown, t["athletes"], "International / Other", "outside the US", "", t["lat"], t["lng"], ""])
        elif not t["state"] and landed:
            # With no comma at all ("Keene", "Aliso Viejo") the state is simply missing: suggest the one the dot landed in, to be checked.
            guess = f"{hometown}, {landed}" if "," not in hometown else ""
            rows.append([hometown, t["athletes"], "International / Other", landed, "", t["lat"], t["lng"], guess])
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows


# Queries to try in order: the hand fix if any; else, for a US place, "town, Full State, USA"; else the cleaned string, then
# first+last parts if there are 3+. A foreign town written with a US-looking abbreviation ("Perth, WA") needs a hand fix.
def candidate_queries(hometown, fixes):
    text = fixes.get(hometown, hometown)       # a hand fix replaces the roster text, then follows the same rules
    parts = hometown_parts(text)
    if not parts:
        return [text]
    state = us_state(text, {})
    if state:
        while parts and re.sub(r"[^a-z]", "", parts[-1].lower()) in _USA_TOKENS:
            parts.pop()
        place = parts[:-1]
        queries = [", ".join(place + [STATE_FULL_NAMES[state], "USA"])]
        if len(place) >= 2:
            queries.append(f"{place[0]}, {STATE_FULL_NAMES[state]}, USA")
        return queries
    if hometown in fixes:
        return [text]
    parts[-1] = _TRAILING_FIXES.get(parts[-1].lower().rstrip("."), parts[-1])
    queries = [", ".join(parts)]
    if len(parts) >= 3:
        queries.append(f"{parts[0]}, {parts[-1]}")
    return queries


# Hometowns Nominatim returned no match for; skipped on later runs so they aren't re-queried every time.
FAILED_PATH = config.HOMETOWN_FAILED_PATH


def load_failed_set():
    """{hometown: reason}. Older files have no reason column."""
    if not os.path.exists(FAILED_PATH):
        return {}
    df = pd.read_csv(FAILED_PATH)
    reasons = df["reason"].fillna("") if "reason" in df.columns else [""] * len(df)
    return dict(zip(df["hometown"], reasons))


def save_failed_set(failed):
    rows = sorted(failed.items())
    pd.DataFrame(rows, columns=["hometown", "reason"]).to_csv(FAILED_PATH, index=False)


# One-time migration, used only when the cache file does not exist (a fresh clone or a lost file).
# After that the cache file is the only source of coordinates and the geo files are output only,
# so deleting a row from the cache really does re-geocode that hometown.
def backfill_cache_from_geo_files(schools):
    frames = []
    for school in schools:
        path = geo_path(school)
        if os.path.exists(path):
            frames.append(pd.read_csv(path, usecols=["hometown", "latitude", "longitude"]))
    if not frames:
        return {}
    valid = pd.concat(frames, ignore_index=True).dropna(subset=["hometown", "latitude", "longitude"])
    valid = valid.drop_duplicates(subset="hometown", keep="first")
    return dict(zip(valid["hometown"], zip(valid["latitude"], valid["longitude"])))


def read_school_rosters(school):
    folder = os.path.join(config.ROSTERS_DIR, school.lower())
    csv_files = sorted(glob.glob(os.path.join(folder, "*.csv")))
    if not csv_files:
        return None

    # Each roster CSV is one flat table.
    frames = [pd.read_csv(path) for path in csv_files]

    raw = pd.concat(frames, ignore_index=True)
    raw = raw[["name", "position", "hometown", "sport", "year"]]
    raw = raw.dropna(subset=["name", "hometown"])      # no name or no hometown: nothing to draw
    raw["year"] = raw["year"].astype("Int64")
    return raw


def geocode_school(school, hometown_cache, failed, transient, fixes):
    folder = os.path.join(config.ROSTERS_DIR, school.lower())
    if not os.path.isdir(folder):
        print(f"{school}: no rosters/{school.lower()} folder, skipped.")
        return

    raw = read_school_rosters(school)
    if raw is None:
        print(f"{school}: no roster files, skipped.")
        return

    # Rebuilt from rosters/ every run so upstream corrections propagate; coordinates come from the cache, so it's cheap.
    rows = raw.copy()
    to_geocode = sorted(h for h in rows["hometown"].unique()
                        if h not in hometown_cache and h not in failed and h not in transient)

    if to_geocode:
        minutes = len(to_geocode) * 1.1 / 60
        print(f"{school}: looking up {len(to_geocode)} new {'hometown' if len(to_geocode) == 1 else 'hometowns'}" + (f" (about {minutes:.0f} min)" if minutes >= 1 else ""))

        geocode = _get_geocode()

        for hometown in to_geocode:
            # A no-match goes on the saved skip list; an error is only skipped for the rest of this run.
            location, errored, doubt = None, False, None
            for query in candidate_queries(hometown, fixes):
                try:
                    location = geocode(query)
                except Exception as e:
                    print(f"  Failed to geocode '{query}': {e}")
                    errored = True
                    break
                if location is not None:
                    # An answer that contradicts the hometown is not cached: it would put a dot in the wrong country.
                    doubt = misplaced_reason(hometown, location.latitude, location.longitude, fixes)
                    if doubt is None:
                        break
                    location = None
            if errored:
                transient.add(hometown)
                continue
            if location is None:
                reason = f"not used: {doubt}" if doubt else "Nominatim found no match"
                print(f"  '{hometown}': {reason}" + ("; add a fix to hometown_fixes.csv, then --retry-failed." if doubt else " -- added to the skip list."))
                failed[hometown] = reason
            else:
                hometown_cache[hometown] = (location.latitude, location.longitude)

    rows["latitude"] = rows["hometown"].map(lambda h: hometown_cache.get(h, (None, None))[0])
    rows["longitude"] = rows["hometown"].map(lambda h: hometown_cache.get(h, (None, None))[1])
    rows = rows.drop_duplicates(subset=["name", "hometown", "sport", "year"], keep="first")

    print(f"{school}: {len(rows):,} rows, {len(to_geocode)} new lookups")
    return rows


# ───────────────────────────── the geo files: only what can be drawn ─────────────────────────────
GEO_COLUMNS = ["name", "position", "hometown", "sport", "year", "latitude", "longitude"]


def unify_hometowns(rows):
    """Spellings of one place ("Powell, OH" / "Powell, Ohio", or a typo like "Wellesly") geocode to the same point. Keep the most common
    spelling at each point, across all schools, or one athlete listed with two spellings becomes two people and two dots."""
    counts = rows.groupby(["latitude", "longitude", "hometown"]).size().reset_index(name="n").sort_values(["n", "hometown"], ascending=[False, True])
    best = {(r.latitude, r.longitude): r.hometown for r in counts.drop_duplicates(["latitude", "longitude"]).itertuples()}
    unified = [best[point] for point in zip(rows["latitude"], rows["longitude"])]
    changed = sum(1 for new, old in zip(unified, rows["hometown"]) if new != old)
    if changed:
        print(f"{changed:,} rows had another spelling of the same place as their hometown; kept the most common one ({len(counts) - len(best):,} spellings merged).")
    rows = rows.copy()
    rows["hometown"] = unified
    return rows


def write_geo_files(frames, failed, transient):
    """Each school's geo file holds only rows that can be drawn: a hometown with coordinates (to 4 decimals, about 10 m), one spelling
    per place, no repeats, in a fixed order. Rows whose hometown has no usable coordinates are left out and reported here."""
    everything = pd.concat([f.assign(school=s) for s, f in frames.items()], ignore_index=True)
    located = everything.dropna(subset=["latitude", "longitude"]).copy()
    located["latitude"], located["longitude"] = located["latitude"].round(4), located["longitude"].round(4)
    located = unify_hometowns(located)
    written = 0
    for school in frames:
        part = located[located["school"] == school]
        part = part.drop_duplicates(subset=["name", "hometown", "sport", "year"]).sort_values(["sport", "year", "name", "hometown"], kind="stable")
        part[GEO_COLUMNS].to_csv(geo_path(school), index=False)
        written += len(part)
    left_out = everything[everything["latitude"].isna()]
    print(f"Geo files: {written:,} rows with coordinates.", end=" ")
    if left_out.empty:
        print("Nothing left out.")
        return
    worst = left_out["hometown"].value_counts().head(8)
    why = lambda h: ((failed.get(h) or "on the skip list, no reason recorded (--retry-failed to look again)") if h in failed
                     else "lookup error, will be retried" if h in transient else "no coordinates yet")
    print(f"Left out: {len(left_out):,} rows from {left_out['hometown'].nunique()} hometowns with no usable coordinates (hometown_failed.csv). Most: "
          + "; ".join(f"{h!r} x{c} ({why(h)})" for h, c in worst.items()))


def geocode_all(retry_failed=False):
    os.makedirs(GEO_DIR, exist_ok=True)
    schools = list(config.bases_df["school"])

    # The cache file is the single source of coordinates. Geo files are only read to rebuild a missing cache.
    if os.path.exists(HOMETOWN_CACHE_PATH):
        hometown_cache = load_hometown_cache_file()
    else:
        hometown_cache = backfill_cache_from_geo_files(schools)
        print(f"No hometown cache file found; rebuilt {len(hometown_cache):,} entries from the geo files (one time).")
        save_hometown_cache_file(hometown_cache)
    failed = {} if retry_failed else load_failed_set()
    transient = set()
    fixes = config.load_fixes()
    print(f"Hometown cache: {len(hometown_cache):,} geocoded, {len(failed)} on the skip list (--retry-failed to retry), {len(fixes)} hand fixes.")
    for problem in config.fix_problems():
        print(f"  hometown_fixes.csv: {problem}")

    frames = {}
    for school in schools:
        # The cache is shared and mutated in place, so later schools reuse earlier lookups.
        rows = geocode_school(school, hometown_cache, failed, transient, fixes)
        # Saved after every school so a crash keeps progress.
        save_hometown_cache_file(hometown_cache)
        save_failed_set(failed)
        if rows is not None:
            frames[school] = rows
    if frames:
        write_geo_files(frames, failed, transient)
        seen = set().union(*(set(f["hometown"]) for f in frames.values()))
        unused = sorted(h for h in fixes if h not in seen)
        if unused:
            print(f"  {len(unused)} hand fixes match no hometown in the rosters (a typo in the left column, or the source page was corrected): "
                  + ", ".join(repr(h) for h in unused[:5]) + (", ..." if len(unused) > 5 else ""))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Geocode every school's rosters, caching each hometown.")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Retry hometowns that had no match on an earlier run.")
    geocode_all(retry_failed=parser.parse_args().retry_failed)
