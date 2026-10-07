"""Rebuilds docs/data.json from geo-rosters/*_rosters_geo.csv (one entry per athlete per sport), then stamps the cache-busting versions in docs/."""
import argparse
import glob
import hashlib
import json
import os
import re

import pandas as pd

import config
from geocode_rosters import us_state

OUT_PATH = os.path.join(config.DOCS_DIR, "data.json")
OTHER_REGION = "International / Other"


# Trims ends and collapses all whitespace (incl. non-breaking spaces); NaN stays NaN.
def squish(series):
    return series.str.split().str.join(" ")


def clean_sport_slug(slug):
    return str(slug).replace("-", " ").title()


def load_all_geo_csvs():
    files = glob.glob(os.path.join(config.GEO_DIR, "*_rosters_geo.csv"))
    if not files:
        raise FileNotFoundError(f"No *_rosters_geo.csv files found in {config.GEO_DIR}")

    frames = []
    for path in files:
        df = pd.read_csv(path)
        df["school"] = re.match(r"^[^_]+", os.path.basename(path)).group().title()
        frames.append(df)

    all_data = pd.concat(frames, ignore_index=True)
    for col in ("name", "position", "hometown", "sport"):
        if col in all_data.columns:
            all_data[col] = squish(all_data[col].astype("string")).astype(object)
    return all_data


# Slugs -> display names via the reference xlsx, falling back to slug cleanup.
def clean_sport_labels(all_data):
    if os.path.exists(config.REFERENCE_PATH):
        refs = pd.read_excel(config.REFERENCE_PATH)[["sport_page", "sport"]].copy()
        refs["sport_display"] = refs["sport"].str.title()
        refs = refs[["sport_page", "sport_display"]]

        all_data = all_data.merge(refs, left_on="sport", right_on="sport_page", how="left")
        all_data["sport"] = all_data["sport_display"].where(
            all_data["sport_display"].notna(),
            all_data["sport"].map(clean_sport_slug),
        )
        all_data = all_data.drop(columns=["sport_page", "sport_display"])
    else:
        print("sport_page_reference.xlsx not found -- cleaning sport slugs instead.")
        all_data["sport"] = all_data["sport"].map(clean_sport_slug)

    return all_data


# Sports shown as one sport whatever the men's, women's, coed or combined roster (matched on the lowercase display name).
COMBINED_SPORTS = {"cross country": "Cross Country", "track and field": "Track and Field", "sailing": "Sailing"}


def combine_label(label):
    low = label.lower()
    for key, name in COMBINED_SPORTS.items():
        if key in low:
            return name
    return label


def combine_sports(all_data):
    before = all_data["sport"].nunique()
    all_data["sport"] = all_data["sport"].map(combine_label)
    print(f"{all_data['sport'].nunique()} sports (combined from {before} labels)")
    return all_data


def clean_and_filter(all_data):
    total = len(all_data)
    all_data = all_data.dropna(subset=["latitude", "longitude"]).copy()
    dropped = total - len(all_data)
    print(f"Read {total:,} roster rows" + (f"; {dropped:,} left off the map for lack of coordinates (see data/state/hometown_failed.csv)." if dropped else "."))
    all_data["latitude"] = all_data["latitude"].round(4)
    all_data["longitude"] = all_data["longitude"].round(4)
    all_data["year"] = all_data["year"].astype(int)
    all_data["name"] = squish(all_data["name"].astype(str))
    all_data["hometown"] = squish(all_data["hometown"].astype(str))
    all_data["sport"] = squish(all_data["sport"].astype(str))

    return all_data


# New run starts after a gap of 3+ years (one skipped year is tolerated as a redshirt).
def assign_run_id(years):
    if len(years) <= 1:
        return [1] * len(years)
    run_ids = [1]
    current = 1
    for i in range(1, len(years)):
        if years[i] - years[i - 1] > 2:
            current += 1
        run_ids.append(current)
    return run_ids


def _sorted_unique_years(s):
    return sorted(int(y) for y in s.unique())


# One entry per (school, sport, name, hometown) run; hometown matches assign_person_ids,
# and position is NOT used because it varies by season.
def collapse_to_athletes(all_data):
    group_cols = ["school", "sport", "name", "hometown"]

    df = all_data.sort_values(
        group_cols + ["year"], ascending=[True, True, True, True, False]
    ).reset_index(drop=True).copy()

    # Row-index loop instead of groupby.apply, which drops group columns on some pandas versions.
    df["run_id"] = 0
    for _, idx in df.groupby(group_cols, dropna=False).groups.items():
        sub_years = df.loc[idx, "year"]
        years_sorted = sorted(int(y) for y in sub_years.unique())
        run_map = dict(zip(years_sorted, assign_run_id(years_sorted)))
        df.loc[idx, "run_id"] = sub_years.map(run_map)

    # Newest year first so "first" below picks the most recent coordinates.
    df = df.sort_values(
        group_cols + ["run_id", "year"], ascending=[True, True, True, True, True, False]
    )

    athletes = (
        df.groupby(group_cols + ["run_id"], dropna=False, sort=False)
        .agg(
            years=("year", _sorted_unique_years),
            latitude=("latitude", "first"),
            longitude=("longitude", "first"),
        )
        .reset_index()
        .drop(columns=["run_id"])
    )

    return athletes


# Team seasons rebuilt by `rosters.py infer`, from the record it keeps (data/state/inferred_seasons.csv).
# Written into data.json so the site marks them with * and needs no copy of this list.
# Sport names get the same treatment as the roster rows, so they match the sport labels in data.json.
def load_inferred_seasons():
    path = config.INFERRED_LOG_PATH
    if not os.path.exists(path):
        return []
    log = pd.read_csv(path, usecols=["school", "sport", "year"]).drop_duplicates()
    if log.empty:
        return []
    log = clean_sport_labels(log)
    log["sport"] = log["sport"].map(combine_label)
    return sorted([r.school, r.sport, int(r.year)] for r in log.drop_duplicates().itertuples())


# One athlete spanning more than four seasons in a sport is usually a real fifth year, but can also be a stale page; `checks.py audit` lists them.
def note_long_careers(athletes, limit=4):
    n = int((athletes["years"].map(len) > limit).sum())
    if n:
        print(f"{n} athlete entries have more than {limit} seasons in one sport; run `python checks.py audit` to review them.")


# Same school + name + hometown = same person; the map draws one dot per person ID.
def assign_person_ids(athletes):
    athletes = athletes.copy()
    athletes["person"] = athletes.groupby(["school", "name", "hometown"], sort=True).ngroup()
    return athletes


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
    s = fixes.get(hometown, hometown)
    s = re.sub(r"\s*\([^)]*\)", "", s)
    s = re.split(r"\s*/\s*|\s+&\s+", s)[0]
    parts = [p.strip() for p in s.split(",") if p.strip()]
    while parts and re.sub(r"[^a-z]", "", parts[-1].lower()) in ("usa", "us", "unitedstates", "unitedstatesofamerica"):
        parts.pop()
    if len(parts) < 2:
        return None
    return _STATE_LOOKUP.get(re.sub(r"[^a-z]", "", parts[-1].lower()))


def load_region_map():
    if not os.path.exists(config.REGIONS_PATH):
        raise FileNotFoundError(f"Missing {config.REGIONS_PATH} (columns: state, region)")
    df = pd.read_csv(config.REGIONS_PATH)
    return dict(zip(df["state"], df["region"])), list(dict.fromkeys(df["region"]))


# Adds a region and state per athlete (regions come from data/inputs/regions.csv) and prints the people per region.
def add_regions(athletes, check_regions=False):
    fixes = config.load_fixes()
    state_region, order = load_region_map()
    region_of = {h: state_region.get(us_state(h, fixes), OTHER_REGION) for h in athletes["hometown"].unique()}
    athletes = athletes.copy()
    athletes["region"] = athletes["hometown"].map(region_of)
    state_of = {h: us_state(h, fixes) or "" for h in athletes["hometown"].unique()}
    athletes["state"] = athletes["hometown"].map(state_of)

    people = athletes.drop_duplicates("person")
    counts = people["region"].value_counts()
    print("Regions: " + " | ".join(f"{region} {int(counts.get(region, 0)):,}" for region in order + [OTHER_REGION]))
    if check_regions:
        other = people[people["region"] == OTHER_REGION]["hometown"].value_counts().head(15)
        print("Most common hometowns in International / Other (look for US towns that failed to parse):\n" + other.to_string())
    return athletes, order + [OTHER_REGION]


def build_payload(athletes, region_order, inferred=None):
    return {
        "person": athletes["person"].astype(int).tolist(),
        "lat": athletes["latitude"].tolist(),
        "lng": athletes["longitude"].tolist(),
        "name": athletes["name"].tolist(),
        "school": athletes["school"].tolist(),
        "sport": athletes["sport"].tolist(),
        "years": athletes["years"].tolist(),
        "hometown": athletes["hometown"].tolist(),
        "region": athletes["region"].tolist(),
        "state": athletes["state"].tolist(),
        "region_order": region_order,
        "inferred": inferred or [],   # [school, sport, year] for each team season rebuilt by `rosters.py infer`
    }


def build_data_json(check_regions=False):
    all_data = load_all_geo_csvs()
    all_data = clean_sport_labels(all_data)
    all_data = combine_sports(all_data)
    all_data = clean_and_filter(all_data)
    athletes = collapse_to_athletes(all_data)
    note_long_careers(athletes)
    athletes = assign_person_ids(athletes)
    print(f"{len(all_data):,} roster rows -> {len(athletes):,} athlete entries -> {athletes['person'].nunique():,} people")
    athletes, region_order = add_regions(athletes, check_regions)
    inferred = load_inferred_seasons()
    if inferred:
        print(f"{len(inferred)} team season(s) were rebuilt by `rosters.py infer` and are marked in data.json.")
    payload = build_payload(athletes, region_order, inferred)

    with open(OUT_PATH, "w") as f:
        json.dump(payload, f, separators=(",", ":"))

    print(f"Wrote {OUT_PATH} ({os.path.getsize(OUT_PATH) / 1e6:.2f} MB)")
    stamp()


# ───────────────────────────── cache versions ─────────────────────────────
# Rewrites the ?v=... cache-busting suffixes in docs/ to content hashes, so browsers refetch exactly the files that changed.
# Runs at the end of every build; after hand-editing anything in docs/, run: python build_data_json.py --stamp-only

def file_hash(name):
    path = os.path.join(config.DOCS_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()[:8]


# Rewrites `name?v=...` for each named file inside one docs/ file; returns True if it changed.
def restamp(target, names):
    path = os.path.join(config.DOCS_DIR, target)
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as f:
        text = f.read()
    new = text
    for name in names:
        digest = file_hash(name)
        if digest:
            new = re.sub(r"(?<![\w.-])" + re.escape(name) + r"\?v=[A-Za-z0-9]+", f"{name}?v={digest}", new)
    if new == text:
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(new)
    return True


# Data files are stamped into the scripts first, because the scripts must be hashed after they embed those versions.
def stamp():
    changed = [js for js in ("app.js", "heatmaps.js") if restamp(js, ["data.json", "us-states.json"])]
    changed += [page for page in ("index.html", "heatmaps.html", "about.html", "about-heatmaps.html")
                if restamp(page, ["shared.js", "app.js", "style.css", "heatmaps.js", "heatmaps.css", "about.css"])]
    print("Cache versions updated in: " + ", ".join(changed) if changed else "Cache versions already current.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild docs/data.json from the geocoded rosters.")
    parser.add_argument("--check-regions", action="store_true",
                        help="Also list the most common hometowns that landed in International / Other, to spot US towns that failed to parse.")
    parser.add_argument("--stamp-only", action="store_true",
                        help="Only refresh the ?v= cache versions in docs/ (after hand-editing a file there); do not rebuild data.json.")
    args = parser.parse_args()
    if args.stamp_only:
        stamp()
    else:
        build_data_json(args.check_regions)
