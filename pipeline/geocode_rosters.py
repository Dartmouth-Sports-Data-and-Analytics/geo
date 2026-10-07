"""geocode_rosters.py — adds coordinates to every school's rosters via Nominatim, caching each hometown.

Also holds the hometown parsing (state and country spellings) that build_data_json.py reuses to work out each athlete's state."""
import argparse
import glob
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
        for h, (lat, lon) in hometown_cache.items()
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


# Splits a hometown into its comma-separated parts, dropping a parenthetical and anything after "/" or "&" ("Paris / Lyon, France" -> Paris).
def hometown_parts(text):
    s = re.sub(r"\s*\([^)]*\)", "", text)
    s = re.split(r"\s*/\s*|\s+&\s+", s)[0]
    return [p.strip() for p in s.split(",") if p.strip()]


# Letters-only lowercase spellings (full names, AP and USPS abbreviations, and spellings seen in the rosters) -> USPS code.
_STATE_NAMES = {
    "AL": "alabama ala al", "AK": "alaska ak", "AZ": "arizona ariz az ari", "AR": "arkansas ark ar",
    "CA": "california calif ca calf cal", "CO": "colorado colo co", "CT": "connecticut conn ct",
    "DE": "delaware del de", "DC": "districtofcolumbia dc", "FL": "florida fla fl", "GA": "georgia ga",
    "HI": "hawaii hi haw", "ID": "idaho ida id", "IL": "illinois ill il", "IN": "indiana ind in", "IA": "iowa ia",
    "KS": "kansas kan kans ks", "KY": "kentucky ky", "LA": "louisiana la", "ME": "maine me",
    "MD": "maryland md", "MA": "massachusetts mass ma", "MI": "michigan mich mi", "MN": "minnesota minn mn",
    "MS": "mississippi miss ms", "MO": "missouri mo", "MT": "montana mont mt", "NE": "nebraska neb nebr ne",
    "NV": "nevada nev nv", "NH": "newhampshire nh", "NJ": "newjersey nj", "NM": "newmexico nm",
    "NY": "newyork ny", "NC": "northcarolina nc", "ND": "northdakota nd", "OH": "ohio oh",
    "OK": "oklahoma okla ok", "OR": "oregon ore or", "PA": "pennsylvania penn pa", "RI": "rhodeisland ri",
    "SC": "southcarolina sc", "SD": "southdakota sd", "TN": "tennessee tenn tn", "TX": "texas tex tx",
    "UT": "utah ut", "VT": "vermont vt", "VA": "virginia vir va", "WA": "washington wash wa",
    "WV": "westvirginia wva wv", "WI": "wisconsin wis wisc wi", "WY": "wyoming wyo wy",
}
_STATE_LOOKUP = {name: code for code, names in _STATE_NAMES.items() for name in names.split()}


# State code from the last comma-separated part of the hometown (after hand fixes); None means not a recognizable US state.
def us_state(hometown, fixes):
    parts = hometown_parts(fixes.get(hometown, hometown))
    while parts and re.sub(r"[^a-z]", "", parts[-1].lower()) in ("usa", "us", "unitedstates", "unitedstatesofamerica"):
        parts.pop()
    if len(parts) < 2:
        return None
    return _STATE_LOOKUP.get(re.sub(r"[^a-z]", "", parts[-1].lower()))


# Queries to try in order: the hand fix if any, else the cleaned string, then first+last parts if there are 3+.
def candidate_queries(hometown, fixes):
    if hometown in fixes:
        return [fixes[hometown]]
    parts = hometown_parts(hometown)
    if not parts:
        return [hometown]
    parts[-1] = _TRAILING_FIXES.get(parts[-1].lower().rstrip("."), parts[-1])
    queries = [", ".join(parts)]
    if len(parts) >= 3:
        queries.append(f"{parts[0]}, {parts[-1]}")
    return queries


# Hometowns Nominatim returned no match for; skipped on later runs so they aren't re-queried every time.
FAILED_PATH = config.HOMETOWN_FAILED_PATH


def load_failed_set():
    if not os.path.exists(FAILED_PATH):
        return set()
    return set(pd.read_csv(FAILED_PATH)["hometown"].dropna())


def save_failed_set(failed):
    pd.DataFrame({"hometown": sorted(failed)}).to_csv(FAILED_PATH, index=False)


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
    raw = raw.dropna(subset=["hometown"])
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

    out_path = geo_path(school)

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
            location, errored = None, False
            for query in candidate_queries(hometown, fixes):
                try:
                    location = geocode(query)
                except Exception as e:
                    print(f"  Failed to geocode '{query}': {e}")
                    errored = True
                    break
                if location is not None:
                    break
            if errored:
                transient.add(hometown)
                continue
            if location is None:
                print(f"  No match for '{hometown}' -- added to the skip list.")
                failed.add(hometown)
            else:
                hometown_cache[hometown] = (location.latitude, location.longitude)

    rows["latitude"] = rows["hometown"].map(lambda h: hometown_cache.get(h, (None, None))[0])
    rows["longitude"] = rows["hometown"].map(lambda h: hometown_cache.get(h, (None, None))[1])
    rows = rows.drop_duplicates(subset=["name", "hometown", "sport", "year"], keep="first")

    rows.to_csv(out_path, index=False)
    missing = rows["latitude"].isna().sum()
    print(f"{school}: {len(rows):,} rows, {len(to_geocode)} new lookups, {missing} without coordinates")


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
    failed = set() if retry_failed else load_failed_set()
    transient = set()
    fixes = config.load_fixes()
    print(f"Hometown cache: {len(hometown_cache):,} geocoded, {len(failed)} on the skip list (--retry-failed to retry), {len(fixes)} hand fixes.")

    for school in schools:
        # The cache is shared and mutated in place, so later schools reuse earlier lookups.
        geocode_school(school, hometown_cache, failed, transient, fixes)
        # Saved after every school so a crash keeps progress.
        save_hometown_cache_file(hometown_cache)
        save_failed_set(failed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Geocode every school's rosters, caching each hometown.")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Retry hometowns that had no match on an earlier run.")
    geocode_all(retry_failed=parser.parse_args().retry_failed)
