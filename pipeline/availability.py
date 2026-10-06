"""The availability cache: which (school, sport page) combos exist and which URL slug works for each, plus the rules that tidy it."""
import os

import pandas as pd

from config import AVAILABILITY_CACHE_PATH
from site_rules import NEUTRAL_ENTRY_CLEANUP_EXEMPT

_AVAILABILITY_COLUMNS = ["school", "sport_page", "available", "resolved_slug"]


def load_availability_cache():
    if os.path.exists(AVAILABILITY_CACHE_PATH):
        df = pd.read_csv(AVAILABILITY_CACHE_PATH)
        for col in _AVAILABILITY_COLUMNS:
            if col not in df.columns:
                df[col] = None
        return df[_AVAILABILITY_COLUMNS]
    return pd.DataFrame(columns=_AVAILABILITY_COLUMNS)


def save_availability_cache(df):
    os.makedirs(os.path.dirname(AVAILABILITY_CACHE_PATH), exist_ok=True)
    df.to_csv(AVAILABILITY_CACHE_PATH, index=False)


def get_cached_row(cache_df, school, sport_page):
    match = cache_df[(cache_df["school"] == school) & (cache_df["sport_page"] == sport_page)]
    return None if match.empty else match.iloc[0]


def record_availability(cache_df, school, sport_page, available, resolved_slug=None):
    # Clear resolved_slug when unavailable so a stale slug never makes a failure look like a success.
    mask = (cache_df["school"] == school) & (cache_df["sport_page"] == sport_page)
    if not available:
        resolved_slug = None
    if mask.any():
        cache_df.loc[mask, "available"] = available
        cache_df.loc[mask, "resolved_slug"] = resolved_slug
    else:
        cache_df.loc[len(cache_df)] = [school, sport_page, available, resolved_slug]
    return cache_df


# If mens-X and womens-X resolve to the same combined page, exclude both rather than mislabel genders.
def resolve_combined_gender_conflicts(cache_df):
    for school in cache_df["school"].unique():
        school_rows = cache_df[cache_df["school"] == school]
        for _, row in school_rows.iterrows():
            sport_page = row["sport_page"]
            if not sport_page.startswith("mens-") or row["available"] != True:
                continue

            sibling = "womens-" + sport_page[len("mens-"):]
            sibling_row = get_cached_row(cache_df, school, sibling)
            if sibling_row is None or sibling_row["available"] != True:
                continue

            if row["resolved_slug"] == sibling_row["resolved_slug"]:
                print(f"[{school}] {sport_page} and {sibling} both resolve to the same "
                      f"combined page ('{row['resolved_slug']}') with no gender field to split "
                      f"on — excluding both rather than duplicating/mislabeling athletes.")
                cache_df = record_availability(cache_df, school, sport_page, False, None)
                cache_df = record_availability(cache_df, school, sibling, False, None)

    return cache_df


def resolve_redundant_neutral_entries(cache_df):
    for school in cache_df["school"].unique():
        school_rows = cache_df[cache_df["school"] == school]
        for _, row in school_rows.iterrows():
            sport_page = row["sport_page"]
            if sport_page.startswith("mens-") or sport_page.startswith("womens-"):
                continue
            if sport_page in NEUTRAL_ENTRY_CLEANUP_EXEMPT:
                continue
            if row["available"] != True:
                continue

            mens_row = get_cached_row(cache_df, school, "mens-" + sport_page)
            womens_row = get_cached_row(cache_df, school, "womens-" + sport_page)
            if mens_row is None or womens_row is None:
                continue
            if mens_row["available"] != True or womens_row["available"] != True:
                continue
            if mens_row["resolved_slug"] == womens_row["resolved_slug"]:
                continue  # that's the OTHER case, already handled above

            print(f"[{school}] {sport_page}: mens-{sport_page} and womens-{sport_page} are "
                  f"already independently available via different pages — excluding this "
                  f"neutral entry so athletes aren't triple-counted.")
            cache_df = record_availability(cache_df, school, sport_page, False, None)

    return cache_df


# If a bare sport and its gendered twin resolve to the same page, keep the bare one and drop the twin.
def resolve_bare_vs_gendered_duplicates(cache_df):
    for school in cache_df["school"].unique():
        school_rows = cache_df[cache_df["school"] == school]
        for _, row in school_rows.iterrows():
            sport_page = row["sport_page"]
            if sport_page.startswith("mens-") or sport_page.startswith("womens-"):
                continue
            if row["available"] != True:
                continue

            for prefix in ("mens-", "womens-"):
                sibling = prefix + sport_page
                sibling_row = get_cached_row(cache_df, school, sibling)
                if sibling_row is None or sibling_row["available"] != True:
                    continue
                if sibling_row["resolved_slug"] != row["resolved_slug"]:
                    continue

                print(f"[{school}] {sibling}: resolves to the exact same page as the "
                      f"existing '{sport_page}' entry ('{row['resolved_slug']}') — this looks "
                      f"like one coed team, not a separate squad. Excluding {sibling} and "
                      f"keeping {sport_page} rather than double-counting the same athletes.")
                cache_df = record_availability(cache_df, school, sibling, False, None)

    return cache_df
