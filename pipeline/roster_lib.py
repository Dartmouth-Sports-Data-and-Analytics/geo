"""Shared constants, exception tables, and scraping helpers for the pipeline scripts; not run directly."""
import os
import random
import re
import threading
import time
from datetime import datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup

YEARS = list(range(2021, 2027))
CURRENT_YEAR = max(YEARS)


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
REFERENCE_PATH = os.path.join(DATA_DIR, "sport_page_reference.xlsx")
# rosters/ is created by pull_rosters.py, not on import.


def _find_sport_reference():
    if os.path.exists(REFERENCE_PATH):
        return REFERENCE_PATH
    raise FileNotFoundError(f"Could not find sport_page_reference.xlsx at {REFERENCE_PATH}.")


sport_refs = pd.read_excel(_find_sport_reference())

# Per (school, sport_page): is it available, and which URL slug worked; delete a row to force a recheck.
AVAILABILITY_CACHE_PATH = os.path.join(DATA_DIR, "_school_sport_availability.csv")
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


# Bare 'rowing' is men's lightweight (its own team), so it is exempt from the neutral-entry cleanup below.
NEUTRAL_ENTRY_CLEANUP_EXEMPT = {"rowing"}


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


# One row per fetch attempt (slug, row count, time), appended as it goes.
SCRAPE_LOG_PATH = os.path.join(DATA_DIR, "_scrape_log.csv")
_SCRAPE_LOG_COLUMNS = ["timestamp", "school", "sport_page", "year", "matched_slug", "row_count"]

# Serializes appends from the per-school threads.
_SCRAPE_LOG_LOCK = threading.Lock()


def log_scrape(school, sport_page, year, matched_slug, row_count):
    os.makedirs(os.path.dirname(SCRAPE_LOG_PATH), exist_ok=True)
    row = pd.DataFrame([[
        datetime.now().isoformat(timespec="seconds"),
        school, sport_page, year, matched_slug, row_count,
    ]], columns=_SCRAPE_LOG_COLUMNS)
    with _SCRAPE_LOG_LOCK:
        write_header = not os.path.exists(SCRAPE_LOG_PATH)
        row.to_csv(SCRAPE_LOG_PATH, mode="a", header=write_header, index=False)


def extract_clean_text(elem):
    if elem is None:
        return None

    for sr in elem.select(".sr-only"):
        sr.extract()

    return elem.get_text(strip=True)


# Fallback slug spellings, tried after the reference file's own slug.
SPORT_SLUG_ALIASES = {
    # 'mrow' is tried at every school; it only costs an extra 404.
    "mens-rowing": ["mens-heavyweight-rowing", "mens-crew", "mrow"],
    # Men's lightweight rowing uses a different slug at each school.
    "rowing": ["mens-lightweight-rowing", "mlr", "lightweight-rowing"],
    "womens-rowing": ["womens-heavyweight-rowing", "womens-crew"],
    "sailing": ["coed-sailing", "csail", "mens-sailing"],
}


# year Y = the Y-(Y+1) academic year for every sport; spring sports use bare-year URL Y+1, and the stored year always equals the requested year.
SPRING_SEASON_SPORTS = {
    "baseball", "softball", "mens-lacrosse", "womens-lacrosse",
    "mens-rowing", "rowing", "womens-rowing", "womens-lightweight-rowing",
}


# Sports whose bare /roster/{year} URL can silently serve wrong content, so dash-year URLs are tried first.
SKIP_BARE_YEAR_SPORTS = {
    # Rowing: Harvard's bare-year trap; dash-year is native elsewhere.
    "mens-rowing",
    "rowing",
    "womens-rowing",
    "womens-lightweight-rowing",
    "sailing",
    "track-and-field",
    # Same trap as sailing.
    "womens-sailing",
    # Identical names across all years at Harvard.
    "mens-golf",
    # Site-side trap confirmed not to be caused by request pacing.
    "field-hockey",
    "football",
    "mens-soccer",
    "equestrian",
    "womens-rugby",

    # Identical rosters across years at Cornell, Harvard, Penn, Yale; harmless where bare-year already works.
    "mens-basketball",
    "womens-basketball",
    "mens-ice-hockey",
    "womens-ice-hockey",
    "mens-squash",
    "womens-squash",
    "mens-swimming-and-diving",
    "womens-swimming-and-diving",
    "mens-tennis",
    "womens-tennis",
    "mens-track-and-field",
    "womens-track-and-field",
    "womens-golf",
    "skiing",
}


# Per-school override: these pages only return correct history via the bare-year URL, so it is tried first.
SCHOOL_PREFERS_BARE_YEAR = {
    ("Harvard", "football"),
    ("Harvard", "field-hockey"),
    ("Harvard", "mens-soccer"),
    ("Penn", "football"),
    ("Penn", "field-hockey"),
    ("Penn", "mens-soccer"),
    ("Yale", "football"),
    ("Yale", "field-hockey"),
    ("Yale", "mens-soccer"),
}


# Dash-year first up to the given year, bare-year after (the site switched URL schemes; each scheme serves the current team for the other era).
SCHOOL_PREFERS_DASH_YEAR = {
    ("Cornell", "mens-cross-country"): 2023,
    ("Cornell", "womens-cross-country"): 2023,
    ("Yale", "womens-volleyball"): 2021,
}


# Seasons with no fetchable roster page, so they are never requested.
KNOWN_MISSING_SEASONS = {
    ("Yale", "mens-golf", 2023),
}

# First varsity year per program; earlier years are never fetched and are dropped from disk.
PROGRAM_FIRST_YEAR = {
    ("Princeton", "womens-rugby"): 2022,
    ("Brown", "mens-golf"): 2026,
    ("Brown", "womens-golf"): 2026,
    ("Brown", "mens-squash"): 2026,
    ("Brown", "womens-squash"): 2026,
}


def _url_candidates(base, sport, slug, year, school):
    """URLs to try for one slug, in order; all are for academic year `year`."""
    dash = f"https://{base}/sports/{slug}/roster/{year}-{str(year + 1)[-2:]}"
    bare_year = year + 1 if sport in SPRING_SEASON_SPORTS else year
    bare = f"https://{base}/sports/{slug}/roster/{bare_year}"
    dash_cutoff = SCHOOL_PREFERS_DASH_YEAR.get((school, sport))
    prefers_dash = dash_cutoff is not None and year <= dash_cutoff
    dash_first = (
        (sport in SKIP_BARE_YEAR_SPORTS or prefers_dash)
        and (school, sport) not in SCHOOL_PREFERS_BARE_YEAR
    )
    return [dash, bare] if dash_first else [bare, dash]


# Escape hatch for pages that return the same roster for every year; currently empty.
CURRENT_SEASON_ONLY = set()


# Combos confirmed by hand as unavailable, skipped before any request; the reason string is for humans only.
KNOWN_UNAVAILABLE = {
    ("Cornell", "skiing"): "confirmed club sport, not varsity",
    ("Cornell", "womens-rugby"): "confirmed club sport, not varsity",
    ("Cornell", "sailing"): "no real separate program behind this slug — see womens-sailing",
    ("Cornell", "womens-golf"): "confirmed not sponsored as a varsity team",

    # Only Harvard and Princeton field women's lightweight rowing.
    ("Brown", "womens-lightweight-rowing"): "confirmed no women's lightweight rowing program — see womens-rowing",
    ("Columbia", "womens-lightweight-rowing"): "confirmed no women's lightweight rowing program — see womens-rowing",
    ("Cornell", "womens-lightweight-rowing"): "confirmed no women's lightweight rowing program — see womens-rowing",
    ("Dartmouth", "womens-lightweight-rowing"): "confirmed no women's lightweight rowing program — see womens-rowing",
    ("Penn", "womens-lightweight-rowing"): "confirmed no women's lightweight rowing program — see womens-rowing",
    ("Yale", "womens-lightweight-rowing"): "confirmed no women's lightweight rowing program — see womens-rowing",

    # Cornell and Penn have one men's rowing team, already under mens-rowing / mens-crew.
    ("Cornell", "rowing"): "no separate lightweight page — their one men's rowing team is already under mens-rowing",
    ("Penn", "rowing"): "no separate lightweight page — their one men's rowing team is already under mens-crew",
}

# Yale's mens-rowing/mens-crew are swapped vs. other schools, so an override is the only slug tried.
SCHOOL_SLUG_OVERRIDES = {
    ("Yale", "mens-rowing"): "mens-crew",
    ("Yale", "rowing"): "mens-rowing",
}


# Confirmed combined (coed) pages: only the neutral entry is scraped.
KNOWN_COMBINED_PAGE = {
    ("Columbia", "cross-country"),
    ("Columbia", "track-and-field"),
    ("Harvard", "cross-country"),
    ("Harvard", "track-and-field"),
}


# Some schools host one combined roster at the bare slug, so a 404 on the gendered slug falls back to the stripped form.
def _degendered_slug(sport):
    for prefix in ("mens-", "womens-"):
        if sport.startswith(prefix):
            return sport[len(prefix):]
    return None


# Slugs never to request as-is; currently empty.
BARE_SLUG_NEVER_REAL = set()


def candidate_slugs(sport):
    """All slugs worth trying for this reference-file sport, in priority order."""
    slugs = [] if sport in BARE_SLUG_NEVER_REAL else [sport]
    degendered = _degendered_slug(sport)
    # The same exclusion applies to degendered fallbacks.
    if degendered and degendered != sport and degendered not in BARE_SLUG_NEVER_REAL:
        slugs.append(degendered)
    for alias in SPORT_SLUG_ALIASES.get(sport, []):
        if alias not in slugs and alias not in BARE_SLUG_NEVER_REAL:
            slugs.append(alias)
    return slugs


# Per-request pause with jitter, plus retries on connection errors, to avoid being throttled.
REQUEST_DELAY_SECONDS = 0.1
REQUEST_DELAY_JITTER = 0.1
# Retries per URL; raise REQUEST_DELAY_* if connection resets return.
MAX_RETRIES = 3

# One shared Session pools connections and is thread-safe.
_SESSION = requests.Session()

# Global minimum gap between any two requests across all threads, so 8 threads don't fire at the same instant.
GLOBAL_MIN_REQUEST_INTERVAL = 0.03
_global_rate_lock = threading.Lock()
_last_global_request_time = [0.0]  # 1-element list so it's mutable from inside the lock below


def _global_rate_limit_wait():
    with _global_rate_lock:
        now = time.monotonic()
        wait_needed = GLOBAL_MIN_REQUEST_INTERVAL - (now - _last_global_request_time[0])
        if wait_needed > 0:
            time.sleep(wait_needed)
        _last_global_request_time[0] = time.monotonic()


# Retry these statuses (rate limits, bot challenges, 5xx); 404 is expected and not retried.
RETRYABLE_STATUS_CODES = {403, 429, 500, 502, 503, 504}


def _get_with_retry(url, headers):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            _global_rate_limit_wait()  # applies on every attempt, including retries
            response = _SESSION.get(url, headers=headers, timeout=15)
        except requests.exceptions.RequestException as e:
            if attempt == MAX_RETRIES:
                raise
            wait = 2 ** attempt  # 2s, 4s, 8s
            print(f"  Connection error on {url} (attempt {attempt}/{MAX_RETRIES}): {e} — retrying in {wait}s...")
            time.sleep(wait)
            continue

        if response.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES:
            wait = 2 ** attempt  # 2s, 4s, 8s
            print(f"  Got status {response.status_code} on {url} (attempt {attempt}/{MAX_RETRIES}) -- "
                  f"treating as a possible temporary rate-limit/server hiccup, not a real 404. "
                  f"Retrying in {wait}s...")
            time.sleep(wait)
            continue

        return response  # a 200, a real 404, or a retryable code out of retries


# Tries each slug's URLs for one sport/year; is_current also allows the bare /roster URL (current season only).
def scrape_roster(base, sport, slugs, year=2025, is_current=False, verbose=False, school=None):

    # CSS selectors for the two roster layouts.
    selectors = [
        "#listPanel > div",
        ".sidearm-roster-player",
    ]

    url_candidates = []
    for slug in slugs:
        url_candidates.extend((slug, url) for url in _url_candidates(base, sport, slug, year, school))
    if is_current and sport not in SPRING_SEASON_SPORTS:
        for slug in slugs:
            # Live /roster page; skipped for spring sports, whose live page is last spring's roster.
            url_candidates.append((slug, f"https://{base}/sports/{slug}/roster"))

    cards = []
    used_selector = None
    used_slug = None

    for slug, url in url_candidates:
        if verbose:
            print("Trying URL:", url)
        time.sleep(REQUEST_DELAY_SECONDS + random.uniform(0, REQUEST_DELAY_JITTER))
        r = _get_with_retry(url, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            if verbose:
                print(f"  -> status {r.status_code}, skipping")
            continue

        soup = BeautifulSoup(r.text, "html.parser")

        for selector in selectors:
            cards = soup.select(selector)
            if verbose:
                print(f"  Trying selector: {selector} — Found {len(cards)} cards")
            if len(cards) > 0:
                used_selector = selector
                used_slug = slug
                break

        if len(cards) > 0:
            break

    if used_selector is None or len(cards) == 0:
        print(f"No roster found for {sport} {year} (tried {len(url_candidates)} URL(s)).")
        return pd.DataFrame(), None

    if verbose:
        print("Using selector:", used_selector)

    if used_slug == _degendered_slug(sport):
        print(
            f"  WARNING: {sport} {year} had no page of its own — matched via the "
            f"combined '{used_slug}' page instead. This roster may list both "
            f"genders together with no field distinguishing them, so it could get "
            f"pulled AGAIN under the other gender's sport label, duplicating every "
            f"athlete. Manually check {base}'s '{used_slug}' page and "
            f"rosters/<school>/{sport}_rosters.csv / {used_slug}_rosters.csv "
            f"against each other before trusting this data."
        )
    elif used_slug != sport:
        print(
            f"  Note: {sport} {year} matched via alias slug '{used_slug}' instead "
            f"of the sport_page_reference.xlsx value '{sport}'."
        )

    # Drop exact-duplicate records within one scrape (a card occasionally renders twice).
    seen = set()
    results = []
    for card in cards:
        player = {}

        # Layout using .sidearm-roster-player.
        if used_selector == ".sidearm-roster-player":
            player["name"] = extract_clean_text(card.select_one(".sidearm-roster-player-name"))
            player["position"] = extract_clean_text(card.select_one(".sidearm-roster-player-position-short"))
            player["class"] = extract_clean_text(card.select_one(".sidearm-roster-player-academic-year"))
            player["height"] = extract_clean_text(card.select_one(".sidearm-roster-player-height"))
            player["weight"] = extract_clean_text(card.select_one(".sidearm-roster-player-weight"))
            player["hometown"] = extract_clean_text(card.select_one(".sidearm-roster-player-hometown"))

        else:
            name_elem = card.select_one("h3")
            if not name_elem:
                continue
            player["name"] = strip_name_badge(name_elem.get_text(strip=True))
            player["position"] = extract_clean_text(
                card.select_one('[data-test-id="s-person-details__bio-stats-person-position-short"]')
            )
            player["class"] = extract_clean_text(
                card.select_one('[data-test-id="s-person-details__bio-stats-person-title"]')
            )
            player["height"] = extract_clean_text(
                card.select_one('[data-test-id="s-person-details__bio-stats-person-season"]')
            )
            player["weight"] = extract_clean_text(
                card.select_one('[data-test-id="s-person-details__bio-stats-person-weight"]')
            )
            player["hometown"] = extract_clean_text(
                card.select_one(".s-person-card__content__location span")
            )

        player["sport"] = sport
        player["year"] = year

        dedup_key = tuple(sorted(player.items()))
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        results.append(player)

    return pd.DataFrame(results), used_slug


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
