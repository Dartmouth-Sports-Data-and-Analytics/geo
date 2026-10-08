"""Fetching and parsing roster pages (URL and slug candidates, polite HTTP, the page parser) and cleaning roster tables."""
import os
import random
import re
import threading
import time
from datetime import datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup

import config

# Whether the last scrape_roster call in this thread hit a rate limit or server error, so an empty result may not be a real "no roster".
_local = threading.local()


def last_fetch_uncertain():
    return getattr(_local, "uncertain", False)


# One row per fetch attempt (slug, row count, time), appended as it goes.
_SCRAPE_LOG_COLUMNS = ["timestamp", "school", "sport_page", "year", "matched_slug", "row_count"]

# Serializes appends from the per-school threads.
_SCRAPE_LOG_LOCK = threading.Lock()


def log_scrape(school, sport_page, year, matched_slug, row_count):
    os.makedirs(os.path.dirname(config.SCRAPE_LOG_PATH), exist_ok=True)
    row = pd.DataFrame([[
        datetime.now().isoformat(timespec="seconds"),
        school, sport_page, year, matched_slug, row_count,
    ]], columns=_SCRAPE_LOG_COLUMNS)
    with _SCRAPE_LOG_LOCK:
        write_header = not os.path.exists(config.SCRAPE_LOG_PATH)
        row.to_csv(config.SCRAPE_LOG_PATH, mode="a", header=write_header, index=False)


def extract_clean_text(elem):
    if elem is None:
        return None

    for sr in elem.select(".sr-only"):
        sr.extract()

    return elem.get_text(strip=True)


# The plain-year and dash URLs for one season, whatever the rules say (`checks.py probe` compares them).
def url_forms(base, sport, slug, year):
    bare_year = year + 1 if sport in config.SPRING_SEASON_SPORTS else year
    return (f"https://{base}/sports/{slug}/roster/{bare_year}",
            f"https://{base}/sports/{slug}/roster/{year}-{str(year + 1)[-2:]}")


def _url_candidates(base, sport, slug, year, school):
    """URLs to try for one slug, in order; all are for academic year `year`."""
    bare, dash = url_forms(base, sport, slug, year)
    for through, form in config.SCHOOL_FORM_THROUGH.get((school, sport), []):
        if year <= through:
            return [dash] if form == "dash" else [bare]
    start = config.SCHOOL_FORM_FROM.get((school, sport))
    if start and year >= start[0]:
        # "From this season on": it keeps applying to every later season, so nothing has to be edited when a new season starts.
        return [dash] if start[1] == "dash" else [bare]
    dash_cutoff = config.SCHOOL_PREFERS_DASH_YEAR.get((school, sport))
    prefers_dash = dash_cutoff is not None and year <= dash_cutoff
    dash_first = (
        (sport in config.SKIP_BARE_YEAR_SPORTS or prefers_dash)
        and (school, sport) not in config.SCHOOL_PREFERS_BARE_YEAR
    )
    return [dash, bare] if dash_first else [bare, dash]


# Some schools host one combined roster at the bare slug, so a 404 on the gendered slug falls back to the stripped form.
def degendered_slug(sport):
    for prefix in ("mens-", "womens-"):
        if sport.startswith(prefix):
            return sport[len(prefix):]
    return None


def candidate_slugs(sport):
    """All slugs worth trying for this reference-file sport, in priority order."""
    slugs = [sport]
    degendered = degendered_slug(sport)
    if degendered and degendered != sport:
        slugs.append(degendered)
    for alias in config.SPORT_SLUG_ALIASES.get(sport, []):
        if alias not in slugs:
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
            print(f"  Connection error on {url} ({type(e).__name__}); retrying in {wait}s (attempt {attempt}/{MAX_RETRIES})")
            time.sleep(wait)
            continue

        if response.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES:
            wait = 2 ** attempt  # 2s, 4s, 8s
            print(f"  Status {response.status_code} on {url}; retrying in {wait}s (attempt {attempt}/{MAX_RETRIES})")
            time.sleep(wait)
            continue

        return response  # a 200, a real 404, or a retryable code out of retries


# Tries each slug's URLs for one sport/year; is_current also allows the bare /roster URL (current season only).
def scrape_roster(base, sport, slugs, year, is_current=False, verbose=False, school=None):

    # CSS selectors for the two roster layouts.
    selectors = [
        "#listPanel > div",
        ".sidearm-roster-player",
    ]

    url_candidates = []
    for slug in slugs:
        url_candidates.extend((slug, url) for url in _url_candidates(base, sport, slug, year, school))
    if is_current and sport not in config.SPRING_SEASON_SPORTS:
        for slug in slugs:
            # Live /roster page; skipped for spring sports, whose live page is last spring's roster.
            url_candidates.append((slug, f"https://{base}/sports/{slug}/roster"))

    cards = []
    used_selector = None
    used_slug = None
    _local.uncertain = False

    for slug, url in url_candidates:
        if verbose:
            print("Trying URL:", url)
        time.sleep(REQUEST_DELAY_SECONDS + random.uniform(0, REQUEST_DELAY_JITTER))
        r = _get_with_retry(url, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            if r.status_code != 404:
                _local.uncertain = True
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
        config.note(f"No roster found for {sport} {year} (tried {len(url_candidates)} URL(s)).")
        return pd.DataFrame(), None

    if verbose:
        print("Using selector:", used_selector)

    if used_slug != sport:
        config.note(f"  {sport} {year} matched via slug '{used_slug}' instead of '{sport}'.")

    # Drop exact-duplicate records within one scrape (a card occasionally renders twice).
    seen = set()
    results = []
    for card in cards:
        player = {}

        # Skip coaches and other staff whose card links to a coaches/staff page.
        link = card.select_one("a[href]")
        if link and STAFF_LINK.search(link.get("href", "")):
            continue
        # Staff cards list an email or phone number; player cards do not.
        if card.select_one('a[href^="mailto:"], a[href^="tel:"]'):
            continue
        # Coach cards also carry the full title ("Robert L. Blackman Head Football Coach") in the position block.
        if is_staff_title(extract_clean_text(card.select_one(".s-person-details__position"))):
            continue

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

        if is_staff_title(player.get("position"), player.get("class")):
            continue

        player["sport"] = sport
        player["year"] = year

        dedup_key = tuple(sorted(player.items()))
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        results.append(player)

    return pd.DataFrame(results), used_slug


# ───────────────────────────── keeping staff out of the rosters ─────────────────────────────

# Roster pages can list coaches, managers and strength staff in the same container as the players. A card is staff if its profile
# link goes to a coaches/staff page, or if its position or class field holds a staff title.
STAFF_LINK = re.compile(r"/(?:coaches|coach|staff)(?:/|$)", re.I)
STAFF_TITLE = re.compile(
    r"coach|manager|strength|conditioning|trainer|athletic training|director|analyst|operations|"
    r"administrator|assistant|sports medicine|physician|equipment|staff",
    re.I,
)


def is_staff_title(*fields):
    """True if any of the text fields reads like a staff title (Head Coach, Student Manager, Strength & Conditioning ...)."""
    return any(isinstance(f, str) and STAFF_TITLE.search(f) for f in fields)


def drop_staff(df):
    """Rows whose position or class is a staff title removed. Returns (table, number dropped). Safe to repeat."""
    cols = [c for c in ("position", "class") if c in df.columns]
    if not cols or df.empty:
        return df, 0
    staff = df[cols].apply(lambda row: is_staff_title(*row), axis=1)
    return df[~staff].reset_index(drop=True), int(staff.sum())


# ───────────────────────────── cleaning and sanity checks for roster tables ─────────────────────────────

# Jersey-number or captain-letter badge glued onto a name ("29Ricky Nunez", "CWilliam Ma"); not initials like "AJ Gaich".
NAME_BADGE_PATTERN = re.compile(r"^(?:[/#]?\d+\s*|[A-Z](?=[A-Z][a-z]))")


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


# Invisible characters pages sometimes carry (zero-width space, joiner, word joiner, byte-order mark); they make two equal names differ.
_INVISIBLE = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")
# Typographic quotes -> plain ones, so "O’Keefe" and "O'Keefe" are the same person and a typed apostrophe finds both.
_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u2032": "'", "\u201c": '"', "\u201d": '"', "\u2033": '"'})


def clean_text(value):
    """One way to write a text field: no invisible characters, plain quotes, single spaces, and blank means missing. Safe to repeat."""
    if not isinstance(value, str):
        return value
    text = " ".join(_INVISIBLE.sub("", value).translate(_QUOTES).split())
    return text if text else float("nan")


def clean_roster_rows(df):
    """Every text column cleaned with clean_text, rows with no name dropped (a card the page parser could not read), and staff rows dropped (see drop_staff).
    Returns (clean table, number of rows dropped). Running it again changes nothing, so rewriting a file never causes churn."""
    out = df.copy()
    for col in out.columns:
        if col != "year":
            out[col] = out[col].map(clean_text)
    out, staff_dropped = drop_staff(out)
    if "name" not in out.columns:
        return out, staff_dropped
    keep = out["name"].notna()
    return out[keep].reset_index(drop=True), int((~keep).sum()) + staff_dropped


# Fully specified row order (year, then name, then every other column), so rewriting a file never reshuffles it.
# Non-year columns are compared as text: freshly scraped values are strings but values read back from disk can be numbers.
def sort_roster(df):
    if df.empty or "year" not in df.columns:
        return df
    order = ["year"] + [c for c in ("name",) if c in df.columns]
    order += [c for c in df.columns if c not in order]
    keys = df[order].astype(str)
    keys["year"] = df["year"]
    return df.loc[keys.sort_values(order, kind="stable").index].reset_index(drop=True)


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
