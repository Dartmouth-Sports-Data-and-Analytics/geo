"""audit_rosters.py — read-only check for seasons that carry last year's seniors forward as grad-labeled athletes."""
import glob
import os
import re

import pandas as pd

import config

OUT_PATH = os.path.join(config.DATA_DIR, "_class_audit.csv")


# Class label -> 1..5 (Fy/So/Jr/Sr/Gr); redshirt prefixes count as the base class; unknown labels give None.
def class_rank(label):
    if pd.isna(label):
        return None
    s = re.sub(r"[^a-z0-9]", "", str(label).lower())
    groups = [("fy", "fr", "rf", "freshman", "firstyear"), ("so", "rso", "sophomore"), ("jr", "rjr", "junior"),
              ("sr", "rs", "senior"), ("gr", "grad", "graduate", "gs", "5th", "6th", "fifthyear", "sixthyear")]
    for candidate in (s, re.sub(r"^(redshirt|r)", "", s)):
        for rank, names in enumerate(groups, start=1):
            if candidate in names:
                return rank
    return None


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


def main():
    df = load_rosters()
    out = audit(df)
    out.to_csv(OUT_PATH, index=False)
    print(f"{len(out)} school/sport/year rosters checked; full table in {OUT_PATH}\n")

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


if __name__ == "__main__":
    main()
