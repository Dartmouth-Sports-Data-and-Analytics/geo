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


def build_payload(athletes):
    return {
        "person": athletes["person"].astype(int).tolist(),
        "lat": athletes["latitude"].tolist(),
        "lng": athletes["longitude"].tolist(),
        "name": athletes["name"].tolist(),
        "school": athletes["school"].tolist(),
        "sport": athletes["sport"].tolist(),
        "years": athletes["years"].tolist(),
        "hometown": athletes["hometown"].tolist(),
    }


def build_data_json():
    all_data = load_all_geo_csvs()
    all_data = clean_sport_labels(all_data)
    all_data = clean_and_filter(all_data)
    athletes = collapse_to_athletes(all_data)
    report_long_careers(athletes)
    athletes = assign_person_ids(athletes)
    payload = build_payload(athletes)

    with open(OUT_PATH, "w") as f:
        json.dump(payload, f, separators=(",", ":"))

    print(f"Wrote {OUT_PATH} ({os.path.getsize(OUT_PATH) / 1e6:.2f} MB)")


if __name__ == "__main__":
    build_data_json()
