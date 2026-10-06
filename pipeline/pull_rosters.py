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


def gather_tasks(cache_df, refresh=frozenset()):
    """Builds the (school, sport, year) fetch list plus per-file state for the write step."""
    tasks = []
    file_state = {}

    for _, srow in config.bases_df.iterrows():
        school, base = srow["school"], srow["site_page"]
        folder_path = school_folder(school)

        for _, row in config.sport_refs().iterrows():
            sport, sport_page = row["sport"], row["sport_page"]

            if (school, sport_page) in rules.KNOWN_UNAVAILABLE:
                reason = rules.KNOWN_UNAVAILABLE[(school, sport_page)]
                print(f"[{school}] {sport}: {reason} — skipping regardless of cache.")
                continue

            cached = availability.get_cached_row(cache_df, school, sport_page)
            if cached is None:
                print(f"[{school}] {sport}: not yet discovered — run discover_sports.py first, skipping.")
                continue
            if cached["available"] == False:
                print(f"[{school}] {sport}: confirmed unavailable, skipping.")
                continue

            resolved_slug = cached["resolved_slug"]
            if not resolved_slug or pd.isna(resolved_slug):
                print(f"[{school}] {sport}: available but no resolved slug on file — re-run discover_sports.py, skipping.")
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
                    print(f"[{school}] {sport}: dropping {len(stale)} stale duplicate-year row set(s) "
                          f"({sorted(stale)}) — this page doesn't vary by year, only the current season is kept.")
                    existing_df = existing_df[existing_df["year"] == config.CURRENT_YEAR]
                    existing_years = set(existing_df["year"].unique())
                    rows_were_trimmed = True
                years_to_scrape = [] if config.CURRENT_YEAR in existing_years else [config.CURRENT_YEAR]
            else:
                years_to_scrape = [
                    year for year in config.YEARS
                    if year >= first_year
                    and (school, sport_page, year) not in rules.KNOWN_MISSING_SEASONS
                    and (year not in existing_years or year == config.CURRENT_YEAR)
                ]

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
                print(f"[{school}] {sport}: already have every year on disk, skipping.")
                continue

            if not years_to_scrape:
                print(f"[{school}] {sport}: no new years to fetch, but will re-save after trimming stale rows.")

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
    total = len(tasks)
    prefix = f"[{label} " if label else "["
    for i, t in enumerate(tasks, 1):
        school, sport, sport_page = t["school"], t["sport"], t["sport_page"]
        year, is_current, resolved_slug = t["year"], t["is_current"], t["resolved_slug"]

        print(f"{prefix}{i}/{total}] Pulling {school} {sport} {year} via '{resolved_slug}'...")
        try:
            df, matched_slug = scraper.scrape_roster(
                t["base"], sport_page, [resolved_slug], year=year, is_current=is_current, school=school
            )
            scraper.log_scrape(school, sport_page, year, matched_slug, len(df))
            if not df.empty:
                file_state[t["key"]]["new_fetches"][year] = df
        except Exception as e:
            print(f"Failed {school} {sport} {year}: {e}")
            scraper.log_scrape(school, sport_page, year, None, 0)


def write_files(file_state):
    for state in file_state.values():
        existing_df = state["existing_df"]
        new_fetches = state["new_fetches"]

        # Freshly fetched years replace what is on disk for that year.
        combined = existing_df
        if not combined.empty and new_fetches:
            combined = combined[~combined["year"].isin(new_fetches.keys())]
        parts = [p for p in [combined, *new_fetches.values()] if not p.empty]

        if not parts:
            print(f"No rosters found for {state['school']} {state['sport']} despite a resolved slug — worth a manual check.")
            continue

        full_df = pd.concat(parts, ignore_index=True)

        # Exact duplicate rows are never real data.
        full_df = full_df.drop_duplicates()

        full_df, dropped = roster_data.drop_stale_years(full_df, config.CURRENT_YEAR)
        if dropped:
            print(f"[{state['school']}] {state['sport']}: dropped year(s) {dropped} -- stale roster "
                  f"(identical to another year, or last year's seniors not advanced), not real data.")
        if full_df.empty:
            print(f"[{state['school']}] {state['sport']}: nothing left after dropping stale years -- not writing a file.")
            continue

        # Heals names saved before the badge fix.
        badges_fixed = roster_data.clean_name_badges(full_df)
        if badges_fixed:
            print(f"[{state['school']}] {state['sport']}: cleaned {badges_fixed} name(s) (badges/extra spaces) in existing rows.")

        if "year" in full_df.columns:
            full_df = full_df.sort_values("year").reset_index(drop=True)

        full_df.to_csv(state["output_file"], index=False)
        print(f"Saved {state['school']} {state['sport']} rosters to {state['output_file']}")


def pull_all(max_workers=8, refresh=frozenset()):
    cache_df = availability.load_availability_cache()
    if cache_df.empty:
        print("No availability data found — run discover_sports.py first.")
        return

    tasks, file_state = gather_tasks(cache_df, refresh=refresh)
    print(f"\n{len(tasks)} (school, sport, year) fetches needed, "
          f"{max_workers} schools running concurrently.\n")

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

    write_files(file_state)


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
    args = parser.parse_args()
    refresh = frozenset(x.strip() for x in args.refresh.split(",") if x.strip())
    pull_all(max_workers=args.workers, refresh=refresh)
