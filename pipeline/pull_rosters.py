"""pull_rosters.py — fetches every season for each school+sport that discovery found available."""
import argparse
import concurrent.futures
import os
from collections import defaultdict

import pandas as pd

import availability
import config
import roster_data
import scraper
import site_rules as rules


# Only this script writes roster files, so it creates the folders.
def school_folder(school):
    folder_path = os.path.join(config.ROSTERS_DIR, school.lower())
    os.makedirs(folder_path, exist_ok=True)
    return folder_path


# Seasons whose last fetch found a real "no roster"; they are not asked for again each run (errors and rate limits are retried).
def confirmed_empty_seasons():
    if not os.path.exists(config.SCRAPE_LOG_PATH):
        return set()
    log = pd.read_csv(config.SCRAPE_LOG_PATH).sort_values("timestamp")
    last = log.drop_duplicates(subset=["school", "sport_page", "year"], keep="last")
    empty = last[(last["row_count"] == 0) & ~last["matched_slug"].isin(["error", "uncertain"])]
    return {(r.school, r.sport_page, int(r.year)) for r in empty.itertuples()}


def gather_tasks(cache_df, refresh=frozenset(), retry_empty=False):
    """Builds the (school, sport, year) fetch list plus per-file state for the write step."""
    tasks = []
    file_state = {}
    not_discovered = []
    known_empty = set() if retry_empty else confirmed_empty_seasons()
    skipped_empty = 0

    for _, srow in config.bases_df.iterrows():
        school, base = srow["school"], srow["site_page"]
        folder_path = school_folder(school)

        for _, row in config.sport_refs().iterrows():
            sport, sport_page = row["sport"], row["sport_page"]

            if (school, sport_page) in rules.KNOWN_UNAVAILABLE:
                reason = rules.KNOWN_UNAVAILABLE[(school, sport_page)]
                config.note(f"[{school}] {sport}: {reason}; skipped.")
                continue

            cached = availability.get_cached_row(cache_df, school, sport_page)
            if cached is None:
                not_discovered.append(f"{school} {sport_page}")
                continue
            if cached["available"] == False:
                config.note(f"[{school}] {sport}: unavailable; skipped.")
                continue

            resolved_slug = cached["resolved_slug"]
            if not resolved_slug or pd.isna(resolved_slug):
                print(f"[{school}] {sport}: available but no slug on file; re-run discover_sports.py.")
                continue

            output_file = os.path.join(folder_path, f"{sport_page}_rosters.csv")
            if "all" in refresh or sport_page in refresh:
                # --refresh: ignore what is on disk and refetch every year.
                existing_df = pd.DataFrame()
            else:
                existing_df = roster_data.load_existing_roster(output_file)

            first_year = rules.PROGRAM_FIRST_YEAR.get((school, sport_page), min(config.YEARS))
            rows_were_trimmed = False
            if not existing_df.empty:
                in_window = existing_df["year"].between(first_year, max(config.YEARS))
                if not in_window.all():
                    gone = sorted(existing_df.loc[~in_window, "year"].unique())
                    print(f"[{school}] {sport}: dropping out-of-window year(s) {gone} from disk.")
                    existing_df = existing_df[in_window]
                    rows_were_trimmed = True
            existing_years = set(existing_df["year"].unique()) if not existing_df.empty else set()

            current_season_only = (school, sport_page) in rules.CURRENT_SEASON_ONLY
            if current_season_only:
                # This page serves identical content for every year, so only the current season is kept.
                stale = existing_years - {config.CURRENT_YEAR}
                if stale:
                    print(f"[{school}] {sport}: dropping stale duplicate years {sorted(stale)}; this page doesn't vary by year.")
                    existing_df = existing_df[existing_df["year"] == config.CURRENT_YEAR]
                    existing_years = set(existing_df["year"].unique())
                    rows_were_trimmed = True
                years_to_scrape = [] if config.CURRENT_YEAR in existing_years else [config.CURRENT_YEAR]
            else:
                forced = "all" in refresh or sport_page in refresh
                wanted = [
                    year for year in config.YEARS
                    if year >= first_year
                    and (school, sport_page, year) not in rules.KNOWN_MISSING_SEASONS
                    and (year not in existing_years or year == config.CURRENT_YEAR)
                ]
                years_to_scrape = [y for y in wanted if forced or y == config.CURRENT_YEAR or (school, sport_page, y) not in known_empty]
                skipped_empty += len(wanted) - len(years_to_scrape)

            key = (school, sport_page)
            file_state[key] = {
                "school": school,
                "sport": sport,
                "output_file": output_file,
                "existing_df": existing_df,
                "new_fetches": {},  # year -> freshly scraped DataFrame, filled in by run_tasks()
                "rows_were_trimmed": rows_were_trimmed,
                "resolved_slug": resolved_slug,
            }

            if not years_to_scrape and not rows_were_trimmed:
                config.note(f"[{school}] {sport}: every year already on disk; skipped.")
                continue

            for year in years_to_scrape:
                tasks.append({
                    "key": key,
                    "school": school,
                    "base": base,
                    "sport": sport,
                    "sport_page": sport_page,
                    "resolved_slug": resolved_slug,
                    "year": year,
                    "is_current": year == config.CURRENT_YEAR,
                })

    if skipped_empty:
        print(f"{skipped_empty} past seasons had no roster last time and are not asked for again (--retry-empty to check them).")
    if not_discovered:
        shown = ", ".join(not_discovered[:6]) + (", ..." if len(not_discovered) > 6 else "")
        print(f"{len(not_discovered)} school + sport pages were never checked; run discover_sports.py: {shown}")
    return tasks, file_state


def _round_robin(tasks, key_fn):
    """Round-robins tasks by key_fn so one bucket never runs back to back."""
    buckets = defaultdict(list)
    for t in tasks:
        buckets[key_fn(t)].append(t)

    ordered = []
    while any(buckets.values()):
        for key in list(buckets.keys()):
            if buckets[key]:
                ordered.append(buckets[key].pop(0))
    return ordered


def interleave_by_sport(tasks):
    """Round-robins one school's tasks by sport_page (the CDN-collision guard)."""
    return _round_robin(tasks, lambda t: t["sport_page"])


def run_tasks(tasks, file_state, label=None):
    empty = 0
    uncertain = 0
    for t in tasks:
        school, sport, sport_page = t["school"], t["sport"], t["sport_page"]
        year, is_current, resolved_slug = t["year"], t["is_current"], t["resolved_slug"]

        config.note(f"[{label}] pulling {sport} {year} via '{resolved_slug}'")
        try:
            df, matched_slug = scraper.scrape_roster(
                t["base"], sport_page, [resolved_slug], year=year, is_current=is_current, school=school
            )
            maybe_error = df.empty and scraper.last_fetch_uncertain()
            scraper.log_scrape(school, sport_page, year, "uncertain" if maybe_error else matched_slug, len(df))
            if df.empty:
                empty += 1
                uncertain += maybe_error
            else:
                file_state[t["key"]]["new_fetches"][year] = df
        except Exception as e:
            empty += 1
            print(f"Failed {school} {sport} {year}: {e}")
            scraper.log_scrape(school, sport_page, year, "error", 0)
    if tasks:
        detail = f", {empty} came back empty" + (f" ({uncertain} after a rate limit or server error; they will be retried next run)" if uncertain else "") if empty else ""
        print(f"[{label}] fetched {len(tasks)} pages{detail}")


def write_files(file_state):
    written = 0
    for state in file_state.values():
        existing_df = state["existing_df"]
        new_fetches = state["new_fetches"]

        # Freshly fetched years replace what is on disk for that year.
        combined = existing_df
        if not combined.empty and new_fetches:
            combined = combined[~combined["year"].isin(new_fetches.keys())]
        parts = [p for p in [combined, *new_fetches.values()] if not p.empty]

        if not parts:
            print(f"[{state['school']}] {state['sport']}: no roster found despite a working slug; worth a manual check.")
            continue

        full_df = pd.concat(parts, ignore_index=True)

        # Exact duplicate rows are never real data.
        full_df = full_df.drop_duplicates()

        full_df, dropped = roster_data.drop_stale_years(full_df, config.CURRENT_YEAR)
        if dropped:
            print(f"[{state['school']}] {state['sport']}: dropped stale year(s) {dropped} (a copy of another year, or last year's seniors not advanced).")
        if full_df.empty:
            print(f"[{state['school']}] {state['sport']}: nothing left after dropping stale years; no file written.")
            continue

        # Heals names saved before the badge fix.
        badges_fixed = roster_data.clean_name_badges(full_df)
        if badges_fixed:
            print(f"[{state['school']}] {state['sport']}: cleaned {badges_fixed} name(s) (badges or extra spaces).")

        if "year" in full_df.columns:
            full_df = full_df.sort_values("year").reset_index(drop=True)

        full_df.to_csv(state["output_file"], index=False)
        config.note(f"[{state['school']}] {state['sport']}: saved {state['output_file']}")
        written += 1
    return written


def pull_all(max_workers=8, refresh=frozenset(), retry_empty=False):
    cache_df = availability.load_availability_cache()
    if cache_df.empty:
        print("No availability data found; run discover_sports.py first.")
        return

    tasks, file_state = gather_tasks(cache_df, refresh=refresh, retry_empty=retry_empty)
    print(f"{len(tasks)} pages to fetch, {max_workers} schools at a time.")

    by_school = defaultdict(list)
    for t in tasks:
        by_school[t["school"]].append(t)

    def run_school(school, school_tasks):
        # Interleave by sport so same-sport, different-year requests never run back to back (CDN cache collisions).
        run_tasks(interleave_by_sport(school_tasks), file_state, label=school)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(run_school, school, school_tasks)
            for school, school_tasks in by_school.items()
        ]
        # .result() surfaces exceptions from worker threads.
        for future in futures:
            future.result()

    written = write_files(file_state)
    print(f"Done: {written} roster files written.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pull every year of roster data for every school+sport confirmed available by discover_sports.py."
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Number of schools to pull concurrently (default: 8, one per school).",
    )
    parser.add_argument(
        "--refresh",
        metavar="SPORT_PAGE[,SPORT_PAGE...]|all",
        default="",
        help="Ignore data already on disk for these sport_pages (or 'all') and refetch every year. "
             "Use after a fix that changes how past years are fetched or labeled, e.g. "
             "--refresh mens-rowing,rowing,womens-rowing,womens-lightweight-rowing",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Print a line for every skipped sport, page fetched and file saved.")
    parser.add_argument("--retry-empty", action="store_true", help="Ask again for past seasons that had no roster last time.")
    args = parser.parse_args()
    config.VERBOSE = args.verbose
    refresh = frozenset(x.strip() for x in args.refresh.split(",") if x.strip())
    pull_all(max_workers=args.workers, refresh=refresh, retry_empty=args.retry_empty)
