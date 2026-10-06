"""Fill a season whose team page is empty on the site, using the seasons either side.

Only athletes on BOTH neighbouring rosters are added, because anyone on the team
before and after the gap must have been on it during the gap. Athletes who were
only on the team in the gap year (a first-year who left, a senior who graduated)
cannot be recovered this way, so the filled season is a minimum, not a full roster.

Idempotent: a team that already has rows for the gap year is left alone. Run it
after pull_rosters.py and before geocode_rosters.py:

    python infer_seasons.py
"""
import glob
import os

import pandas as pd

import config

try:
    from roster_data import strip_name_badge
except ImportError:                      # fall back to plain whitespace cleanup
    strip_name_badge = lambda s: s

# (school, sport_page, gap_year, year_before, year_after); years are academic start years
INFERRED_SEASONS = [
    ("Yale", "mens-golf", 2023, 2022, 2024),
]

LABELS = ["Fy.", "So.", "Jr.", "Sr."]
CLASS_INDEX = {"fy": 0, "fr": 0, "first year": 0, "firstyear": 0, "freshman": 0,
               "so": 1, "sophomore": 1, "jr": 2, "junior": 2, "sr": 3, "senior": 3}


def class_idx(value):
    return CLASS_INDEX.get(str(value).strip().lower().replace(".", ""))


def find_col(df, options, what):
    for c in options:
        if c in df.columns:
            return c
    raise SystemExit(f"Cannot find the {what} column. Columns are: {list(df.columns)}")


def roster_path(school, sport):
    hits = glob.glob(os.path.join(config.DATA_DIR, "rosters", "*", f"{sport}_rosters.csv"))
    hits = [h for h in hits if os.path.basename(os.path.dirname(h)).lower() == school.lower()]
    return hits[0] if hits else None


def key(name):
    return " ".join(str(strip_name_badge(name)).lower().split())


def fill(school, sport, gap, before, after, log):
    path = roster_path(school, sport)
    if not path:
        print(f"{school} {sport}: no roster file found, skipped")
        return
    df = pd.read_csv(path)
    year_c = find_col(df, ["year"], "year")
    name_c = find_col(df, ["name"], "name")
    class_c = find_col(df, ["class", "cl", "academic_year", "class_year"], "class")
    if (df[year_c] == gap).any():
        print(f"{school} {sport} {gap}: already has rows, left alone")
        return
    b, a = df[df[year_c] == before].copy(), df[df[year_c] == after].copy()
    if b.empty or a.empty:
        print(f"{school} {sport}: need both {before} and {after} rosters, skipped")
        return
    b["_k"], a["_k"] = b[name_c].map(key), a[name_c].map(key)
    both = a[a["_k"].isin(set(b["_k"]))].drop_duplicates("_k").copy()
    before_cls = dict(zip(b["_k"], b[class_c]))
    new_cls = []
    for _, r in both.iterrows():
        i_after = class_idx(r[class_c])
        i_before = class_idx(before_cls.get(r["_k"]))
        if i_after is not None and i_after > 0:
            new_cls.append(LABELS[i_after - 1])
        elif i_before is not None and i_before < 3:
            new_cls.append(LABELS[i_before + 1])
        else:
            new_cls.append("")
    both[class_c] = new_cls
    both[year_c] = gap
    out = both.drop(columns="_k")
    pd.concat([df, out], ignore_index=True).to_csv(path, index=False)
    for n in out[name_c]:
        log.append({"school": school, "sport": sport, "year": gap, "name": n})
    print(f"{school} {sport} {gap}: added {len(out)} athletes on both the {before} and {after} rosters "
          f"({len(b)} in {before}, {len(a)} in {after})")


def main():
    log = []
    for spec in INFERRED_SEASONS:
        fill(*spec, log)
    if log:
        p = os.path.join(config.DATA_DIR, "_inferred_seasons.csv")
        pd.DataFrame(log).to_csv(p, index=False)
        print(f"List of added athletes: {p}")


if __name__ == "__main__":
    main()
