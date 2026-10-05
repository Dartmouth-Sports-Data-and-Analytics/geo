"""discover_sports.py — finds which school+sport roster pages exist and which URL slug works for each."""
import argparse
import concurrent.futures
import contextlib
import threading

import pandas as pd

import roster_lib as lib


def discover_combo(school, base, sport, sport_page, cache_df, lock=None, verbose=False):
    # No lock for single-threaded callers.
    lock = lock if lock is not None else contextlib.nullcontext()

    if (school, sport_page) in lib.KNOWN_UNAVAILABLE:
        reason = lib.KNOWN_UNAVAILABLE[(school, sport_page)]
        print(f"[{school}] {sport}: {reason} — forcing unavailable, no request made.")
        with lock:
            cache_df = lib.record_availability(cache_df, school, sport_page, False, None)
            lib.save_availability_cache(cache_df)
        return cache_df

    # Skip gendered rows whose combined page is already confirmed.
    degendered = sport_page
    for prefix in ("mens-", "womens-"):
        if sport_page.startswith(prefix):
            degendered = sport_page[len(prefix):]
            break
    if (school, degendered) in lib.KNOWN_COMBINED_PAGE and degendered != sport_page:
        print(f"[{school}] {sport}: confirmed combined page (no gender split) — "
              f"forcing unavailable, no request made. See '{degendered}' instead.")
        with lock:
            cache_df = lib.record_availability(cache_df, school, sport_page, False, None)
            lib.save_availability_cache(cache_df)
        return cache_df

    # A hand-confirmed override is the only slug tried.
    override = lib.SCHOOL_SLUG_OVERRIDES.get((school, sport_page))
    if override:
        slugs = [override]
    else:
        slugs = lib.candidate_slugs(sport_page)

    # For known combined pages, also try the men's slug (Harvard track-and-field lives there).
    if not override and (school, sport_page) in lib.KNOWN_COMBINED_PAGE:
        gendered_candidate = f"mens-{sport_page}"
        if gendered_candidate not in slugs:
            slugs.append(gendered_candidate)

    # Try the last working slug first (reorder only; every combo is still re-verified); skipped under an override so a stale slug cannot return.
    if not override:
        with lock:
            cached = lib.get_cached_row(cache_df, school, sport_page)
            if cached is not None and cached["available"] == True:
                last_slug = cached["resolved_slug"]
                if last_slug and not pd.isna(last_slug):
                    if last_slug in slugs:
                        slugs.remove(last_slug)
                    slugs.insert(0, last_slug)

    print(f"[{school}] {sport}: checking {slugs}...")

    # The HTTP request runs outside the lock so schools run in parallel.
    df, matched_slug = lib.scrape_roster(
        base, sport_page, slugs, year=lib.CURRENT_YEAR, is_current=True, verbose=verbose, school=school
    )
    if df.empty:
        # Spring rosters aren't posted in the fall, so check last season before declaring a combo unavailable.
        print(f"  -> nothing for {lib.CURRENT_YEAR} yet, checking {lib.CURRENT_YEAR - 1}...")
        df, matched_slug = lib.scrape_roster(
            base, sport_page, slugs, year=lib.CURRENT_YEAR - 1, is_current=False, verbose=verbose, school=school
        )

    with lock:
        if df.empty:
            print(f"  -> not available.")
            cache_df = lib.record_availability(cache_df, school, sport_page, False, None)
        else:
            print(f"  -> available, resolved slug: '{matched_slug}' ({len(df)} rows).")
            cache_df = lib.record_availability(cache_df, school, sport_page, True, matched_slug)

        lib.save_availability_cache(cache_df)  # save after every combo, not just at the end

    return cache_df


def discover_all(max_workers=8, verbose_sport_page=None):
    cache_df = lib.load_availability_cache()
    lock = threading.Lock()

    # One thread per school; --verbose-sport logs one sport's URLs inside a real full run.
    def process_school(school, base):
        for _, sport_row in lib.sport_refs.iterrows():
            sport, sport_page = sport_row["sport"], sport_row["sport_page"]
            verbose = (sport_page == verbose_sport_page)
            discover_combo(school, base, sport, sport_page, cache_df, lock=lock, verbose=verbose)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(process_school, school_row["school"], school_row["site_page"])
            for _, school_row in lib.bases_df.iterrows()
        ]
        # .result() surfaces exceptions from worker threads.
        for future in futures:
            future.result()

    with lock:
        cache_df = lib.resolve_combined_gender_conflicts(cache_df)
        cache_df = lib.resolve_redundant_neutral_entries(cache_df)
        cache_df = lib.resolve_bare_vs_gendered_duplicates(cache_df)
        lib.save_availability_cache(cache_df)

    print("\nDiscovery complete. See data/_school_sport_availability.csv.")


# Re-run one combo with full URL/status logging.
def recheck_combo(school, sport_page):
    school_row = lib.bases_df[lib.bases_df["school"] == school]
    if school_row.empty:
        print(f"Unknown school '{school}'. Valid schools: {list(lib.bases_df['school'])}")
        return
    base = school_row.iloc[0]["site_page"]

    sport_row = lib.sport_refs[lib.sport_refs["sport_page"] == sport_page]
    sport = sport_row.iloc[0]["sport"] if not sport_row.empty else sport_page

    cache_df = lib.load_availability_cache()
    print(f"Rechecking [{school}] {sport} ({sport_page}) with verbose URL/status logging...\n")
    cache_df = discover_combo(school, base, sport, sport_page, cache_df, verbose=True)
    print(f"\nUpdated cache row for ({school}, {sport_page}):")
    print(lib.get_cached_row(cache_df, school, sport_page))


def main():
    parser = argparse.ArgumentParser(
        description="Discover which school+sport roster pages exist and which slug works for each."
    )
    parser.add_argument(
        "--recheck",
        metavar="SCHOOL,SPORT_PAGE",
        default=None,
        help="Diagnose a single combo instead of running full discovery. "
             "Re-checks just this (school, sport_page) with full verbose "
             "URL/status/selector logging, e.g. --recheck Harvard,rowing. "
             "Always single-threaded regardless of --workers.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Number of schools to discover concurrently (default: 8, one per school).",
    )
    parser.add_argument(
        "--verbose-sport",
        metavar="SPORT_PAGE",
        default=None,
        help="Run a REAL full discovery (all schools, multi-threaded, as normal) but "
             "with full URL/status/selector logging for every school's attempt at this "
             "one sport_page. For diagnosing a combo that only fails inside a full run "
             "and can't be reproduced via an isolated --recheck, e.g. --verbose-sport rowing",
    )
    args = parser.parse_args()

    if args.recheck:
        school, sport_page = [s.strip() for s in args.recheck.split(",", 1)]
        recheck_combo(school, sport_page)
    else:
        discover_all(max_workers=args.workers, verbose_sport_page=args.verbose_sport)


if __name__ == "__main__":
    main()
