"""Fill a season whose team page is empty on the site, using the seasons either side.

Who is added to the gap year:
  1. Athletes on BOTH neighbouring rosters (they must have been on the team in between).
  2. Juniors on the earlier roster who are not on the later one: almost certainly seniors
     in the gap year who then graduated.            (ADD_JUNIORS)
  3. Sophomores on the later roster who are not on the earlier one: almost certainly
     first-years in the gap year.                   (ADD_SOPHOMORES)
Not recoverable: anyone on the team only in the gap year, and seniors on the earlier roster
who took a fifth year. So the filled season is still a minimum, not a full roster.

Safe to rerun. Rows previously added by this script (listed in data/_inferred_seasons.csv)
are replaced; if the gap year ever has real rows from the site, the team is left alone.
Run after pull_rosters.py and before geocode_rosters.py:

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
ADD_JUNIORS = True
ADD_SOPHOMORES = True

LABELS = ["Fy.", "So.", "Jr.", "Sr."]
CLASS_INDEX = {"fy": 0, "fr": 0, "first year": 0, "firstyear": 0, "freshman": 0,
               "so": 1, "sophomore": 1, "jr": 2, "junior": 2, "sr": 3, "senior": 3}
LOG_COLS = ["school", "sport", "year", "name", "rule"]


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


def fill(school, sport, gap, before, after, prior):
    """Returns the log rows added, or None if the team was skipped."""
    path = roster_path(school, sport)
    if not path:
        print(f"{school} {sport}: no roster file found, skipped")
        return None
    df = pd.read_csv(path)
    year_c = find_col(df, ["year"], "year")
    name_c = find_col(df, ["name"], "name")
    class_c = find_col(df, ["class", "cl", "academic_year", "class_year"], "class")

    mine = prior[(prior["school"] == school) & (prior["sport"] == sport) & (prior["year"] == gap)]
    ours = {key(n) for n in mine["name"]}
    existing = df[df[year_c] == gap]
    if len(existing):
        real = existing[~existing[name_c].map(key).isin(ours)]
        if len(real):
            print(f"{school} {sport} {gap}: has {len(real)} rows from the site, left alone")
            return None
        df = df[df[year_c] != gap]            # rebuild our own earlier fill

    b, a = df[df[year_c] == before].copy(), df[df[year_c] == after].copy()
    if b.empty or a.empty:
        print(f"{school} {sport}: need both {before} and {after} rosters, skipped")
        return None
    b["_k"], a["_k"] = b[name_c].map(key), a[name_c].map(key)
    b, a = b.drop_duplicates("_k"), a.drop_duplicates("_k")
    in_b, in_a = set(b["_k"]), set(a["_k"])

    added = []                                 # (row, class label, rule)
    for _, r in a.iterrows():                  # on both, or a sophomore who is new
        i = class_idx(r[class_c])
        if r["_k"] in in_b:
            before_i = class_idx(b.loc[b["_k"] == r["_k"], class_c].iloc[0])
            lab = LABELS[i - 1] if i else (LABELS[before_i + 1] if before_i is not None and before_i < 3 else "")
            added.append((r, lab, "both"))
        elif ADD_SOPHOMORES and i == 1:
            added.append((r, LABELS[0], "sophomore"))
    for _, r in b.iterrows():                  # a junior who then graduated
        if r["_k"] not in in_a and ADD_JUNIORS and class_idx(r[class_c]) == 2:
            added.append((r, LABELS[3], "junior"))

    rows = []
    for r, lab, rule in added:
        row = r.drop(labels="_k").copy()
        row[class_c], row[year_c] = lab, gap
        rows.append(row)
    out = pd.DataFrame(rows)
    pd.concat([df, out], ignore_index=True).to_csv(path, index=False)

    counts = pd.Series([rule for _, _, rule in added]).value_counts().to_dict()
    print(f"{school} {sport} {gap}: {len(out)} athletes ({counts}) from {len(b)} on the {before} roster "
          f"and {len(a)} on the {after} roster")
    return [{"school": school, "sport": sport, "year": gap, "name": r[name_c], "rule": rule}
            for r, _, rule in added]


def main():
    log_path = os.path.join(config.DATA_DIR, "_inferred_seasons.csv")
    prior = pd.read_csv(log_path) if os.path.exists(log_path) else pd.DataFrame(columns=LOG_COLS)
    for c in LOG_COLS:
        if c not in prior.columns:
            prior[c] = ""
    result = prior.copy()
    for school, sport, gap, before, after in INFERRED_SEASONS:
        new = fill(school, sport, gap, before, after, prior)
        if new is not None:
            same = (result["school"] == school) & (result["sport"] == sport) & (result["year"] == gap)
            result = pd.concat([result[~same], pd.DataFrame(new, columns=LOG_COLS)], ignore_index=True)
    result.to_csv(log_path, index=False)
    print(f"Added athletes are listed in {log_path}")


if __name__ == "__main__":
    main()
