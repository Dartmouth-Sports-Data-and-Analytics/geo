"""Fetching and parsing roster pages: URL and slug candidates, polite HTTP, and the page parser."""
import os
import random
import threading
import time
from datetime import datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup

import config
from roster_data import strip_name_badge
from site_rules import (BARE_SLUG_NEVER_REAL, SCHOOL_FORM_THROUGH, SCHOOL_PREFERS_BARE_YEAR, SCHOOL_PREFERS_DASH_YEAR,
                        SKIP_BARE_YEAR_SPORTS, SPORT_SLUG_ALIASES, SPRING_SEASON_SPORTS)

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


# The plain-year and dash URLs for one season, whatever the rules say (probe_year.py compares them).
def url_forms(base, sport, slug, year):
    bare_year = year + 1 if sport in SPRING_SEASON_SPORTS else year
    return (f"https://{base}/sports/{slug}/roster/{bare_year}",
            f"https://{base}/sports/{slug}/roster/{year}-{str(year + 1)[-2:]}")


def _url_candidates(base, sport, slug, year, school):
    """URLs to try for one slug, in order; all are for academic year `year`."""
    bare, dash = url_forms(base, sport, slug, year)
    for through, form in SCHOOL_FORM_THROUGH.get((school, sport), []):
        if year <= through:
            return [dash] if form == "dash" else [bare]
    dash_cutoff = SCHOOL_PREFERS_DASH_YEAR.get((school, sport))
    prefers_dash = dash_cutoff is not None and year <= dash_cutoff
    dash_first = (
        (sport in SKIP_BARE_YEAR_SPORTS or prefers_dash)
        and (school, sport) not in SCHOOL_PREFERS_BARE_YEAR
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
    slugs = [] if sport in BARE_SLUG_NEVER_REAL else [sport]
    degendered = degendered_slug(sport)
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
