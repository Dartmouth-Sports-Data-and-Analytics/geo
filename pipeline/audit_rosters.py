"""audit_rosters.py — read-only check for seasons that carry last year's seniors forward as grad-labeled athletes.

Everything it prints is also saved to data/_audit_report.txt (overwritten each run)."""
import datetime
import glob
import os
import sys

import pandas as pd

import config
from roster_data import class_rank
from site_rules import KNOWN_MISSING_SEASONS

OUT_PATH = os.path.join(config.DATA_DIR, "_class_audit.csv")
REPORT_PATH = os.path.join(config.DATA_DIR, "_audit_report.txt")


# Copies everything printed to the report file as well as the terminal.
class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for s in self.streams:
            s.write(text)

    def flush(self):
        for s in self.streams:
            s.flush()


def load_rosters():
    frames = []
    for path in glob.glob(os.path.join(config.ROSTERS_DIR, "*", "*_rosters.csv")):
        df = pd.read_csv(path).reindex(columns=["name", "class", "hometown", "year"])
        df["school"] = os.path.basename(os.path.dirname(path)).title()
        df["sport"] = os.path.basename(path)[:-len("_rosters.csv")]
        frames.append(df)
    if not frames:
        raise FileNotFoundError(f"No roster files found in {config.ROSTERS_DIR}")
    df = pd.concat(frames, ignore_index=True)
    df["name"] = df["name"].astype(str).str.split().str.join(" ")
    df["rank"] = df["class"].map(class_rank)
    return df


# One row per (school, sport, year): size, grad-labeled count, and how many of last year's seniors reappear.
def audit(df):
    rows = []
    for (school, sport), g in df.groupby(["school", "sport"]):
        by_year = {int(y): sub for y, sub in g.groupby("year")}
        for y, cur in sorted(by_year.items()):
            prev = by_year.get(y - 1)
            seniors = set(prev.loc[prev["rank"] == 4, "name"]) if prev is not None else set()
            back = seniors & set(cur["name"])
            rows.append({
                "school": school, "sport": sport, "year": y, "roster_size": len(cur),
                "grad_labeled": int((cur["rank"] == 5).sum()),
                "prior_seniors": len(seniors), "seniors_returning": len(back),
                "pct_returning": round(100 * len(back) / len(seniors)) if seniors else None,
            })
    return pd.DataFrame(rows)


# Class labels by year for athletes with 5+ seasons in one sport, flagging any label that failed to advance year to year.
def long_careers(df, limit=4):
    rows = []
    for (school, sport, name, _), g in df.groupby(["school", "sport", "name", "hometown"], dropna=False):
        g = g.sort_values("year").drop_duplicates("year")
        if len(g) <= limit:
            continue
        ranks = list(g["rank"])
        stuck = any(a is not None and a == b and not pd.isna(a) and not pd.isna(b) for a, b in zip(ranks, ranks[1:]))
        rows.append({
            "school": school, "sport": sport, "name": name, "first": int(g["year"].iloc[0]), "last": int(g["year"].iloc[-1]),
            "classes": " ".join(f"{int(y) % 100}:{c}" for y, c in zip(g["year"], g["class"])), "label_not_advanced": stuck,
        })
    return pd.DataFrame(rows)


# Roster rows per school and season (a COVID dip shows up here), plus teams missing a season between two seasons that exist.
def coverage(df):
    table = df.groupby(["school", "year"]).size().unstack(fill_value=0)
    table.to_csv(os.path.join(config.DATA_DIR, "_coverage.csv"))
    print("\nRoster rows per school and season (2016 = 2016-17):")
    print(table.to_string())

    gaps = []
    for (school, sport), g in df.groupby(["school", "sport"]):
        have = set(g["year"].astype(int))
        missing = [y for y in range(min(have), max(have) + 1) if y not in have and (school, sport, y) not in KNOWN_MISSING_SEASONS]
        if missing:
            gaps.append((school, sport, missing))
    covid = [g for g in gaps if g[2] == [2020]]
    other = [g for g in gaps if g[2] != [2020]]
    print(f"\n{len(covid)} teams have no 2020-21 roster but do have seasons on both sides of it (expected after COVID).")
    print(f"{len(other)} teams are missing seasons between ones that exist, other than the known-missing ones in site_rules.py" + (":" if other else "."))
    for school, sport, missing in other[:25]:
        print(f"  {school} {sport}: missing {missing}")
    if len(other) > 25:
        print(f"  ... and {len(other) - 25} more")


# Players on two consecutive rosters should have moved up exactly one class; a season where most did not is probably copied, shifted by a year or mislabeled.
def class_advancement(df, min_returning=6, threshold=0.5):
    rows = []
    known = df[df["rank"].between(1, 4)]
    for (school, sport), g in known.groupby(["school", "sport"]):
        ranks = {int(y): sub.drop_duplicates("name").set_index("name")["rank"] for y, sub in g.groupby("year")}
        for year, current in sorted(ranks.items()):
            previous = ranks.get(year - 1)
            if previous is None:
                continue
            both = current.index.intersection(previous.index)
            if len(both) < min_returning:
                continue
            change = current[both] - previous[both]
            moved = int((change == 1).sum())
            rows.append({"school": school, "sport": sport, "year": year, "returning": len(both), "same": int((change == 0).sum()),
                         "moved_up_one": moved, "plus_two": int((change == 2).sum()), "pct": round(100 * moved / len(both))})
    out = pd.DataFrame(rows)
    if out.empty:
        return
    out.to_csv(os.path.join(config.DATA_DIR, "_class_advancement.csv"), index=False)
    bad = out[out["pct"] < 100 * threshold].sort_values("pct")
    print(f"\nSeasons where under {threshold:.0%} of returning players moved up one class ({len(bad)} of {len(out)}; 2021 is expected after COVID).\nOne odd season shows up twice in a row (it is the season both rows share). same = class unchanged (stale labels or a skipped COVID year); plus_two = labels catching up after COVID:")
    print(bad.head(30).to_string(index=False) if len(bad) else "  none")
    if len(bad) > 30:
        print(f"  ... and {len(bad) - 30} more in data/_class_advancement.csv")


def run():
    df = load_rosters()
    out = audit(df)
    out.to_csv(OUT_PATH, index=False)
    print(f"{len(out)} school/sport/year rosters checked; full table in {OUT_PATH}\n")

    coverage(df)
    class_advancement(df)

    # Seniors returning en masse is the carry-over signature; real returns (5th years) are a small share.
    sus = out[(out["prior_seniors"] >= 3) & (out["pct_returning"] >= 50)].sort_values("pct_returning", ascending=False)
    print(f"Rosters where 50%+ of last year's seniors reappear ({len(sus)}):")
    print(sus.to_string(index=False) if len(sus) else "  none")

    print("\nGrad-labeled athletes by year (all schools/sports):")
    print(out.groupby("year")[["grad_labeled"]].sum().join(out[out["grad_labeled"] > 0].groupby("year").size().rename("rosters_with_any")).fillna(0).astype(int).to_string())

    print("\nRosters with the most grad-labeled athletes:")
    print(out.sort_values("grad_labeled", ascending=False).head(10)[["school", "sport", "year", "grad_labeled", "roster_size"]].to_string(index=False))

    lc = long_careers(df)
    if len(lc):
        lc.sort_values(["label_not_advanced", "school", "sport"], ascending=[False, True, True]).to_csv(
            os.path.join(config.DATA_DIR, "_long_careers.csv"), index=False)
        stuck = lc[lc["label_not_advanced"]]
        print(f"\n{len(lc)} athletes have 5+ seasons in one sport; {len(stuck)} have a class label that did not advance:")
        print(stuck.head(30).to_string(index=False) if len(stuck) else "  none")
        print("Full list: data/_long_careers.csv")

    unknown = df[df["rank"].isna() & df["class"].notna()]["class"].value_counts().head(10)
    if len(unknown):
        print("\nClass labels not recognized (top 10):")
        print(unknown.to_string())


def main():
    with open(REPORT_PATH, "w", encoding="utf-8") as report:
        report.write(f"Audit run {datetime.datetime.now():%Y-%m-%d %H:%M}\n\n")
        terminal = sys.stdout
        sys.stdout = Tee(terminal, report)
        try:
            run()
        finally:
            sys.stdout = terminal
    print(f"\nReport saved to {REPORT_PATH}")


if __name__ == "__main__":
    main()
