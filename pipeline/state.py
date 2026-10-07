"""What the pipeline remembers between runs: which school + sport pages exist (and their URL slug), and which past seasons have no roster page.

Both live in data/state/ and are committed to git.
"""
import os
import threading
from datetime import date

import pandas as pd

import config

# ───────────────────────────── availability cache (data/state/school_sport_availability.csv) ─────────────────────────────
_AVAILABILITY_COLUMNS = ["school", "sport_page", "available", "resolved_slug"]


def load_availability_cache():
    if os.path.exists(config.AVAILABILITY_CACHE_PATH):
        df = pd.read_csv(config.AVAILABILITY_CACHE_PATH)
        for col in _AVAILABILITY_COLUMNS:
            if col not in df.columns:
                df[col] = None
        return df[_AVAILABILITY_COLUMNS]
    return pd.DataFrame(columns=_AVAILABILITY_COLUMNS)


def save_availability_cache(df):
    os.makedirs(os.path.dirname(config.AVAILABILITY_CACHE_PATH), exist_ok=True)
    # Sorted so the file never reshuffles with the order the school threads finish in (no git noise).
    df.sort_values(["school", "sport_page"]).to_csv(config.AVAILABILITY_CACHE_PATH, index=False)


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


# ───────────────────────────── seasons with no roster page (data/state/season_status.csv) ─────────────────────────────
# Past seasons whose roster page really does not exist, so they are not asked for every run. The scrape log is only a log:
# nothing reads it except the one-time migration below.
#   - Only a confirmed "no page" is remembered. Rate limits and server errors never are.
#   - The current season is never remembered (it is always asked for again, and a page that is missing today may exist
#     next month). Without this, last year's "not posted yet" would become a permanent gap when CURRENT_YEAR moves on.
#   - A season that later returns a roster is removed from the file.
_STATUS_COLUMNS = ["school", "sport_page", "year", "last_checked"]

_lock = threading.Lock()
_state = None      # (school, sport_page, year) -> date last confirmed missing; loaded on first use
_dirty = False


def _from_scrape_log():
    """One-time migration: the seasons whose latest logged fetch found a real 'no roster'."""
    if not os.path.exists(config.SCRAPE_LOG_PATH):
        return {}
    log = pd.read_csv(config.SCRAPE_LOG_PATH).sort_values("timestamp", kind="stable")
    last = log.drop_duplicates(subset=["school", "sport_page", "year"], keep="last")
    empty = last[(last["row_count"] == 0) & ~last["matched_slug"].isin(["error", "uncertain"])]
    return {(r.school, r.sport_page, int(r.year)): str(r.timestamp)[:10]
            for r in empty.itertuples() if int(r.year) < config.CURRENT_YEAR}


def _load():
    global _state, _dirty
    if _state is not None:
        return
    if os.path.exists(config.SEASON_STATUS_PATH):
        df = pd.read_csv(config.SEASON_STATUS_PATH)
        _state = {(r.school, r.sport_page, int(r.year)): r.last_checked for r in df.itertuples()}
    else:
        _state = _from_scrape_log()
        if _state:
            print(f"No {os.path.basename(config.SEASON_STATUS_PATH)} yet; built it from the scrape log ({len(_state)} seasons with no roster page).")
        _dirty = True          # write the file on the first save, even if it is empty


def no_roster_seasons():
    """Set of (school, sport_page, year) known to have no roster page."""
    with _lock:
        _load()
        return set(_state)


def mark_no_roster(school, sport_page, year):
    global _dirty
    year = int(year)
    if year >= config.CURRENT_YEAR:
        return
    with _lock:
        _load()
        _state[(school, sport_page, year)] = date.today().isoformat()
        _dirty = True


def clear_no_roster(school, sport_page, year):
    global _dirty
    with _lock:
        _load()
        if _state.pop((school, sport_page, int(year)), None) is not None:
            _dirty = True


def save_season_status():
    """Write the file if anything changed. Atomic, so a crash never leaves a half-written file."""
    global _dirty
    with _lock:
        if _state is None or not _dirty:
            return
        rows = [[s, sp, y, d] for (s, sp, y), d in sorted(_state.items())]
        os.makedirs(os.path.dirname(config.SEASON_STATUS_PATH), exist_ok=True)
        tmp = config.SEASON_STATUS_PATH + ".tmp"
        pd.DataFrame(rows, columns=_STATUS_COLUMNS).to_csv(tmp, index=False)
        os.replace(tmp, config.SEASON_STATUS_PATH)
        _dirty = False
