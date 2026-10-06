"""Paths, school list and seasons for the pipeline, plus loaders for the hand-edited input files."""
import functools
import os

import pandas as pd

VERBOSE = False  # set by --verbose; per-item progress goes through note(), summaries and warnings through print()


def note(*args):
    if VERBOSE:
        print(*args)


YEARS = list(range(2016, 2027))  # season = academic year start, so 2016 is 2016-17
CURRENT_YEAR = max(YEARS)
# Older seasons discovery tries when a page has nothing for the current season, so teams that were later cut are still found.
PROBE_YEARS = [CURRENT_YEAR - 1, 2021, 2019, YEARS[0]]

# Ivy League athletics site hosts.
bases = {
    "school": ["Brown", "Columbia", "Cornell", "Dartmouth", "Harvard", "Penn", "Princeton", "Yale"],
    "site_page": ["brownbears.com", "gocolumbialions.com", "cornellbigred.com", "dartmouthsports.com", "gocrimson.com", "pennathletics.com",
                  "goprincetontigers.com", "yalebulldogs.com"]
}
bases_df = pd.DataFrame(bases)

# Layout: <root>/pipeline (code), <root>/data (inputs + outputs), <root>/docs (published site); anchored to this file.
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
ROSTERS_DIR = os.path.join(DATA_DIR, "rosters")
GEO_DIR = os.path.join(DATA_DIR, "geo-rosters")
DOCS_DIR = os.path.join(ROOT_DIR, "docs")
# rosters/ is created by pull_rosters.py, not on import.

# Hand-edited inputs.
REFERENCE_PATH = os.path.join(DATA_DIR, "sport_page_reference.xlsx")
FIXES_PATH = os.path.join(DATA_DIR, "hometown_fixes.csv")
REGIONS_PATH = os.path.join(DATA_DIR, "regions.csv")

# Pipeline state.
AVAILABILITY_CACHE_PATH = os.path.join(DATA_DIR, "_school_sport_availability.csv")
SCRAPE_LOG_PATH = os.path.join(DATA_DIR, "_scrape_log.csv")


@functools.cache
def sport_refs():
    """sport_page_reference.xlsx (columns sport, sport_page), read on first use so importing this module never needs it."""
    if not os.path.exists(REFERENCE_PATH):
        raise FileNotFoundError(f"Could not find sport_page_reference.xlsx at {REFERENCE_PATH}.")
    return pd.read_excel(REFERENCE_PATH)


# Hand-made hometown corrections: {original text: corrected text}.
def load_fixes():
    if not os.path.exists(FIXES_PATH):
        return {}
    df = pd.read_csv(FIXES_PATH).dropna(subset=["hometown", "corrected"])
    return dict(zip(df["hometown"], df["corrected"]))
