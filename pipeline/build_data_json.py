"""Rebuilds docs/data.json from geo-rosters/*_rosters_geo.csv (one entry per athlete per sport)."""
import glob
import json
import os
import re

import pandas as pd

# Not importing roster_lib: it requires sport_page_reference.xlsx at import time, which is optional here.
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
GEO_DIR = os.path.join(DATA_DIR, "geo-rosters")
REF_PATH = os.path.join(DATA_DIR, "sport_page_reference.xlsx")
OUT_PATH = os.path.join(ROOT_DIR, "docs", "data.json")
REGIONS_PATH = os.path.join(DATA_DIR, "regions.csv")
FIXES_PATH = os.path.join(DATA_DIR, "hometown_fixes.csv")
OTHER_REGION = "International / Other"


# Trims ends and collapses all whitespace (incl. non-breaking spaces); NaN stays NaN.
def squish(series):
    return series.str.split().str.join(" ")


def clean_sport_slug(slug):
    return str(slug).replace("-", " ").title()


def load_all_geo_csvs():
    files = glob.glob(os.path.join(GEO_DIR, "*_rosters_geo.csv"))
    if not files:
        raise FileNotFoundError(f"No *_rosters_geo.csv files found in {GEO_DIR}")

    frames = []
    for path in files:
        df = pd.read_csv(path)
        df["school"] = re.match(r"^[^_]+", os.path.basename(path)).group().title()
        frames.append(df)

    all_data = pd.concat(frames, ignore_index=True)
    for col in ("name", "position", "hometown", "sport"):
        if col in all_data.columns:
            all_data[col] = squish(all_data[col].astype("string")).astype(object)
    print(f"{len(all_data)} rows loaded from {len(files)} school file(s)")
    return all_data


# Slugs -> display names via the reference xlsx, falling back to slug cleanup.
def clean_sport_labels(all_data):
    if os.path.exists(REF_PATH):
        refs = pd.read_excel(REF_PATH)[["sport_page", "sport"]].copy()
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

    print("Sports:", sorted(all_data["sport"].unique()))
    return all_data


# Sports shown as one sport whatever the men's, women's, coed or combined roster (matched on the lowercase display name).
COMBINED_SPORTS = {"cross country": "Cross Country", "track and field": "Track and Field", "sailing": "Sailing"}


def combine_sports(all_data):
    def combine(label):
        low = label.lower()
        for key, name in COMBINED_SPORTS.items():
            if key in low:
                return name
        return label

    before = all_data["sport"].nunique()
    all_data["sport"] = all_data["sport"].map(combine)
    print(f"Sports combined: {before} -> {all_data['sport'].nunique()} distinct sport labels")
    return all_data


def clean_and_filter(all_data):
    all_data = all_data.dropna(subset=["latitude", "longitude"]).copy()
    all_data["latitude"] = all_data["latitude"].round(4)
    all_data["longitude"] = all_data["longitude"].round(4)
    all_data["year"] = all_data["year"].astype(int)
    all_data["name"] = squish(all_data["name"].astype(str))
    all_data["hometown"] = squish(all_data["hometown"].astype(str))
    all_data["sport"] = squish(all_data["sport"].astype(str))

    print(f"Rows: {len(all_data)} across {all_data['school'].nunique()} schools")
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

    print(f"{len(all_data)} roster rows -> {len(athletes)} athlete entries")
    return athletes


# Flags athletes with more than 4 seasons in one sport (likely stale pages or merged namesakes), grouped to expose systemic causes.
def report_long_careers(athletes, limit=4):
    long = athletes[athletes["years"].map(len) > limit]
    if long.empty:
        print(f"No athlete has more than {limit} seasons in one sport.")
        return
    print(f"\n{len(long)} athlete entries have more than {limit} seasons in one sport:")
    counts = long.groupby(["school", "sport"]).size().sort_values(ascending=False)
    for (school, sport), n in counts.head(15).items():
        print(f"  {school} / {sport}: {n}")
    for _, r in long.sort_values(["school", "sport", "name"]).head(25).iterrows():
        print(f"    {r['name']} ({r['school']}, {r['sport']}): {r['years']}")
    print()


# Same school + name + hometown = same person; the map draws one dot per person ID.
def assign_person_ids(athletes):
    athletes = athletes.copy()
    athletes["person"] = athletes.groupby(["school", "name", "hometown"], sort=True).ngroup()
    n_people = athletes["person"].nunique()
    print(f"{len(athletes)} athlete entries -> {n_people} distinct people "
          f"({len(athletes) - n_people} entries are a second+ sport for someone)")
    return athletes


# Letters-only lowercase spellings (full names, AP and USPS abbreviations, and spellings seen in the rosters) -> USPS code.
_STATE_NAMES = {
    "AL": "alabama ala al", "AK": "alaska ak", "AZ": "arizona ariz az ari", "AR": "arkansas ark ar",
    "CA": "california calif ca calf cal", "CO": "colorado colo co", "CT": "connecticut conn ct",
    "DE": "delaware del de", "DC": "districtofcolumbia dc", "FL": "florida fla fl", "GA": "georgia ga",
    "HI": "hawaii hi", "ID": "idaho ida id", "IL": "illinois ill il", "IN": "indiana ind in", "IA": "iowa ia",
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


def load_fixes():
    if not os.path.exists(FIXES_PATH):
        return {}
    df = pd.read_csv(FIXES_PATH).dropna(subset=["hometown", "corrected"])
    return dict(zip(df["hometown"], df["corrected"]))


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
    if not os.path.exists(REGIONS_PATH):
        raise FileNotFoundError(f"Missing {REGIONS_PATH} (columns: state, region)")
    df = pd.read_csv(REGIONS_PATH)
    return dict(zip(df["state"], df["region"])), list(dict.fromkeys(df["region"]))


# Adds a region per athlete (from data/regions.csv) and prints a summary plus the most common unrecognized hometowns.
def add_regions(athletes):
    fixes = load_fixes()
    state_region, order = load_region_map()
    region_of = {h: state_region.get(us_state(h, fixes), OTHER_REGION) for h in athletes["hometown"].unique()}
    athletes = athletes.copy()
    athletes["region"] = athletes["hometown"].map(region_of)
    state_of = {h: us_state(h, fixes) or "" for h in athletes["hometown"].unique()}
    athletes["state"] = athletes["hometown"].map(state_of)

    people = athletes.drop_duplicates("person")
    counts = people["region"].value_counts()
    print("\nPeople by region:")
    for region in order + [OTHER_REGION]:
        print(f"  {region}: {int(counts.get(region, 0))}")
    other = people[people["region"] == OTHER_REGION]["hometown"].value_counts().head(15)
    if len(other):
        print("Most common hometowns in International / Other (check for US towns that failed to parse):")
        print(other.to_string())
    print()
    return athletes, order + [OTHER_REGION]


def build_payload(athletes, region_order):
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
    }


def build_data_json():
    all_data = load_all_geo_csvs()
    all_data = clean_sport_labels(all_data)
    all_data = combine_sports(all_data)
    all_data = clean_and_filter(all_data)
    athletes = collapse_to_athletes(all_data)
    report_long_careers(athletes)
    athletes = assign_person_ids(athletes)
    athletes, region_order = add_regions(athletes)
    payload = build_payload(athletes, region_order)

    with open(OUT_PATH, "w") as f:
        json.dump(payload, f, separators=(",", ":"))

    print(f"Wrote {OUT_PATH} ({os.path.getsize(OUT_PATH) / 1e6:.2f} MB)")


if __name__ == "__main__":
    build_data_json()
