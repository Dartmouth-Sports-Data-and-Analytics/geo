"""Cleaning and sanity checks for roster tables."""
import os
import re

import pandas as pd


# Jersey-number or captain-letter badge glued onto a name ("29Ricky Nunez", "CWilliam Ma"); not initials like "AJ Gaich".
NAME_BADGE_PATTERN = re.compile(r"^(?:\d+\s*|[A-Z](?=[A-Z][a-z]))")


def strip_name_badge(raw_name):
    # Loop until no badges remain (cards can stack both); whitespace is collapsed first.
    name = " ".join(str(raw_name).split())
    while True:
        stripped = NAME_BADGE_PATTERN.sub("", name, count=1)
        if stripped == name:
            return name
        name = stripped


# One flat CSV per school+sport with year as a column; returns empty if missing or unreadable.
def load_existing_roster(output_file):
    if not os.path.exists(output_file):
        return pd.DataFrame()
    try:
        df = pd.read_csv(output_file)
        if "year" in df.columns:
            df["year"] = df["year"].astype(int)
        return df
    except Exception as e:
        print(f"  Could not read existing {output_file} ({e}), will refetch all years.")
        return pd.DataFrame()


# Drop years whose (name, class) set matches another year's -- stale or trapped pages, never real data.
def drop_stale_years(df, current_year):
    if df.empty or "name" not in df.columns or "year" not in df.columns:
        return df, []
    cls = df["class"].astype(str) if "class" in df.columns else ""
    sig_df = df.assign(_sig=df["name"].astype(str) + "|" + cls)
    sigs = {y: frozenset(g["_sig"]) for y, g in sig_df.groupby("year")}
    drop = set()
    cur = sigs.get(current_year)
    if cur is not None and sigs.get(current_year - 1) == cur:
        drop.add(current_year)
    elif cur is not None:
        drop |= {y for y, s in sigs.items() if y != current_year and s == cur}
    # Last year's seniors back and still labeled "Sr." means classes never advanced, so the current page is last season's team.
    if cur is not None and current_year not in drop and "class" in df.columns:
        senior = df["class"].astype(str).str.lower().str.replace(r"[^a-z]", "", regex=True).eq("sr")
        prior_seniors = set(df.loc[(df["year"] == current_year - 1) & senior, "name"])
        still_seniors = set(df.loc[(df["year"] == current_year) & senior, "name"]) & prior_seniors
        if len(prior_seniors) >= 3 and len(still_seniors) / len(prior_seniors) >= 0.5:
            drop.add(current_year)
    past = [y for y in sigs if y != current_year and y not in drop]
    for y in past:
        if any(o != y and sigs[o] == sigs[y] for o in past):
            drop.add(y)
    return df[~df["year"].isin(drop)], sorted(drop)


# Strips badges/extra spaces from names (healing rows saved before the fix); returns the count changed.
def clean_name_badges(df):
    if df.empty or "name" not in df.columns:
        return 0
    fixed = df["name"].apply(strip_name_badge)
    total_fixed = (fixed != df["name"].astype(str)).sum()
    df["name"] = fixed
    return total_fixed


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
