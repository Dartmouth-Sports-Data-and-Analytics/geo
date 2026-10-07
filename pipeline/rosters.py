"""rosters.py: the three steps that build the roster files.

    python rosters.py discover   find which school + sport pages exist and which URL slug works for each
    python rosters.py pull       download every season for each page discover found
    python rosters.py infer      fill a season whose team page is blank, from the seasons either side

Run them in that order (run_pipeline.py does), then geocode_rosters.py and build_data_json.py.
"""
import argparse
import concurrent.futures
import contextlib
import glob
import os
import threading
from collections import defaultdict

import pandas as pd

import config
import scraper
import state



# ───────────────────────── discover ─────────────────────────

# Finds which school+sport roster pages exist and which URL slug works for each.

def discover_combo(school, base, sport, sport_page, cache_df, lock=None, verbose=False):
    # No lock for single-threaded callers.
    lock = lock if lock is not None else contextlib.nullcontext()

    if (school, sport_page) in config.KNOWN_UNAVAILABLE:
        reason = config.KNOWN_UNAVAILABLE[(school, sport_page)]
        config.note(f"[{school}] {sport}: {reason}; marked unavailable without a request.")
        with lock:
            cache_df = state.record_availability(cache_df, school, sport_page, False, None)
            state.save_availability_cache(cache_df)
        return cache_df

    # Skip gendered rows whose combined page is already confirmed.
    degendered = sport_page
    for prefix in ("mens-", "womens-"):
        if sport_page.startswith(prefix):
            degendered = sport_page[len(prefix):]
            break
    if (school, degendered) in config.KNOWN_COMBINED_PAGE and degendered != sport_page:
        config.note(f"[{school}] {sport}: combined page, see '{degendered}'; marked unavailable without a request.")
        with lock:
            cache_df = state.record_availability(cache_df, school, sport_page, False, None)
            state.save_availability_cache(cache_df)
        return cache_df

    # A hand-confirmed override is the only slug tried.
    override = config.SCHOOL_SLUG_OVERRIDES.get((school, sport_page))
    if override:
        slugs = [override]
    else:
        slugs = scraper.candidate_slugs(sport_page)

    # For known combined pages, also try the men's slug (Harvard track-and-field lives there).
    if not override and (school, sport_page) in config.KNOWN_COMBINED_PAGE:
        gendered_candidate = f"mens-{sport_page}"
        if gendered_candidate not in slugs:
            slugs.append(gendered_candidate)

    # Try the last working slug first (reorder only; every combo is still re-verified); skipped under an override so a stale slug cannot return.
    if not override:
        with lock:
            cached = state.get_cached_row(cache_df, school, sport_page)
            if cached is not None and cached["available"] == True:
                last_slug = cached["resolved_slug"]
                if last_slug and not pd.isna(last_slug):
                    if last_slug in slugs:
                        slugs.remove(last_slug)
                    slugs.insert(0, last_slug)

    config.note(f"[{school}] {sport}: checking {slugs}")

    # The HTTP request runs outside the lock so schools run in parallel.
    df, matched_slug = scraper.scrape_roster(
        base, sport_page, slugs, year=config.CURRENT_YEAR, is_current=True, verbose=verbose, school=school
    )
    # Spring rosters aren't posted in the fall and cut teams have no recent page, so look back before declaring a combo unavailable.
    for year in config.PROBE_YEARS:
        if not df.empty:
            break
        config.note(f"  nothing yet, checking {year}")
        df, matched_slug = scraper.scrape_roster(
            base, sport_page, slugs, year=year, is_current=False, verbose=verbose, school=school
        )

    with lock:
        if df.empty:
            config.note("  not available")
            cache_df = state.record_availability(cache_df, school, sport_page, False, None)
        else:
            config.note(f"  available via '{matched_slug}' ({len(df)} rows)")
            cache_df = state.record_availability(cache_df, school, sport_page, True, matched_slug)

        state.save_availability_cache(cache_df)  # save after every combo, not just at the end

    return cache_df


# Totals, plus every combo whose availability or slug differs from the previous run (nothing is listed on a first run).
def summarize_discovery(before, after):
    def statuses(df):
        return {(r.school, r.sport_page): (bool(r.available), None if pd.isna(r.resolved_slug) else r.resolved_slug) for r in df.itertuples()}

    def describe(status):
        return f"available via '{status[1]}'" if status[0] else "unavailable"

    old, new = statuses(before), statuses(after)
    found = sum(1 for status in new.values() if status[0])
    print(f"Discovery complete: {len(new)} school + sport pages checked, {found} available, {len(new) - found} not.")
    if not old:
        return
    changes = [f"  {school} {page}: {describe(old[(school, page)]) if (school, page) in old else 'new'} -> {describe(status)}"
               for (school, page), status in sorted(new.items()) if old.get((school, page)) != status]
    print("Changes since the last run:\n" + "\n".join(changes) if changes else "No changes since the last run.")


def discover_all(max_workers=8, verbose_sport_page=None):
    cache_df = state.load_availability_cache()
    before = cache_df.copy()
    lock = threading.Lock()

    # One thread per school; --verbose-sport logs one sport's URLs inside a real full run.
    def process_school(school, base):
        for _, sport_row in config.sport_refs().iterrows():
            sport, sport_page = sport_row["sport"], sport_row["sport_page"]
            verbose = (sport_page == verbose_sport_page)
            discover_combo(school, base, sport, sport_page, cache_df, lock=lock, verbose=verbose)
        with lock:
            rows = cache_df[cache_df["school"] == school]
        print(f"[{school}] {int((rows['available'] == True).sum())} of {len(rows)} sports available")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(process_school, school_row["school"], school_row["site_page"])
            for _, school_row in config.bases_df.iterrows()
        ]
        # .result() surfaces exceptions from worker threads.
        for future in futures:
            future.result()

    with lock:
        cache_df = resolve_combined_gender_conflicts(cache_df)
        cache_df = resolve_redundant_neutral_entries(cache_df)
        cache_df = resolve_bare_vs_gendered_duplicates(cache_df)
        state.save_availability_cache(cache_df)

    summarize_discovery(before, cache_df)


# Re-run one combo with full URL/status logging.
def recheck_combo(school, sport_page):
    school_row = config.bases_df[config.bases_df["school"] == school]
    if school_row.empty:
        print(f"Unknown school '{school}'. Valid schools: {list(config.bases_df['school'])}")
        return
    base = school_row.iloc[0]["site_page"]

    refs = config.sport_refs()
    sport_row = refs[refs["sport_page"] == sport_page]
    sport = sport_row.iloc[0]["sport"] if not sport_row.empty else sport_page

    cache_df = state.load_availability_cache()
    config.VERBOSE = True
    print(f"Rechecking [{school}] {sport} ({sport_page}) with full URL and status logging...")
    cache_df = discover_combo(school, base, sport, sport_page, cache_df, verbose=True)
    print(f"\nUpdated cache row for ({school}, {sport_page}):")
    print(state.get_cached_row(cache_df, school, sport_page))


# If mens-X and womens-X resolve to the same combined page, exclude both rather than mislabel genders.
def resolve_combined_gender_conflicts(cache_df):
    for school in cache_df["school"].unique():
        school_rows = cache_df[cache_df["school"] == school]
        for _, row in school_rows.iterrows():
            sport_page = row["sport_page"]
            if not sport_page.startswith("mens-") or row["available"] != True:
                continue

            sibling = "womens-" + sport_page[len("mens-"):]
            sibling_row = state.get_cached_row(cache_df, school, sibling)
            if sibling_row is None or sibling_row["available"] != True:
                continue

            if row["resolved_slug"] == sibling_row["resolved_slug"]:
                config.note(f"[{school}] {sport_page} and {sibling} resolve to the same combined page ('{row['resolved_slug']}'); "
                            f"excluding both rather than mislabeling athletes.")
                cache_df = state.record_availability(cache_df, school, sport_page, False, None)
                cache_df = state.record_availability(cache_df, school, sibling, False, None)

    return cache_df


def resolve_redundant_neutral_entries(cache_df):
    for school in cache_df["school"].unique():
        school_rows = cache_df[cache_df["school"] == school]
        for _, row in school_rows.iterrows():
            sport_page = row["sport_page"]
            if sport_page.startswith("mens-") or sport_page.startswith("womens-"):
                continue
            if sport_page in config.NEUTRAL_ENTRY_CLEANUP_EXEMPT:
                continue
            if row["available"] != True:
                continue

            mens_row = state.get_cached_row(cache_df, school, "mens-" + sport_page)
            womens_row = state.get_cached_row(cache_df, school, "womens-" + sport_page)
            if mens_row is None or womens_row is None:
                continue
            if mens_row["available"] != True or womens_row["available"] != True:
                continue
            if mens_row["resolved_slug"] == womens_row["resolved_slug"]:
                continue  # that's the OTHER case, already handled above

            config.note(f"[{school}] {sport_page}: mens- and womens- pages already exist separately; excluding this neutral entry.")
            cache_df = state.record_availability(cache_df, school, sport_page, False, None)

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
                sibling_row = state.get_cached_row(cache_df, school, sibling)
                if sibling_row is None or sibling_row["available"] != True:
                    continue
                if sibling_row["resolved_slug"] != row["resolved_slug"]:
                    continue

                config.note(f"[{school}] {sibling} resolves to the same page as '{sport_page}' ('{row['resolved_slug']}'); "
                            f"treating it as one coed team and excluding {sibling}.")
                cache_df = state.record_availability(cache_df, school, sibling, False, None)

    return cache_df


# ───────────────────────── pull ─────────────────────────

# Only this script writes roster files, so it creates the folders.
def school_folder(school):
    folder_path = os.path.join(config.ROSTERS_DIR, school.lower())
    os.makedirs(folder_path, exist_ok=True)
    return folder_path


def gather_tasks(cache_df, refresh=frozenset(), retry_empty=False):
    """Builds the (school, sport, year) fetch list plus per-file state for the write step."""
    tasks = []
    file_state = {}
    not_discovered = []
    known_empty = set() if retry_empty else state.no_roster_seasons()
    skipped_empty = 0
    combined = []

    for _, srow in config.bases_df.iterrows():
        school, base = srow["school"], srow["site_page"]
        folder_path = school_folder(school)

        for _, row in config.sport_refs().iterrows():
            sport, sport_page = row["sport"], row["sport_page"]

            if (school, sport_page) in config.KNOWN_UNAVAILABLE:
                reason = config.KNOWN_UNAVAILABLE[(school, sport_page)]
                config.note(f"[{school}] {sport}: {reason}; skipped.")
                continue

            cached = state.get_cached_row(cache_df, school, sport_page)
            if cached is None:
                not_discovered.append(f"{school} {sport_page}")
                continue
            if cached["available"] == False:
                config.note(f"[{school}] {sport}: unavailable; skipped.")
                continue

            resolved_slug = cached["resolved_slug"]
            if not resolved_slug or pd.isna(resolved_slug):
                print(f"[{school}] {sport}: available but no slug on file; re-run `rosters.py discover`.")
                continue

            if resolved_slug == scraper.degendered_slug(sport_page):
                combined.append(f"{school} {sport_page} -> {resolved_slug}")
            output_file = os.path.join(folder_path, f"{sport_page}_rosters.csv")
            if "all" in refresh or sport_page in refresh:
                # --refresh: ignore what is on disk and refetch every year.
                existing_df = pd.DataFrame()
            else:
                existing_df = scraper.load_existing_roster(output_file)

            first_year = config.PROGRAM_FIRST_YEAR.get((school, sport_page), min(config.YEARS))
            rows_were_trimmed = False
            if not existing_df.empty:
                in_window = existing_df["year"].between(first_year, max(config.YEARS))
                if not in_window.all():
                    gone = sorted(existing_df.loc[~in_window, "year"].unique())
                    print(f"[{school}] {sport}: dropping out-of-window year(s) {gone} from disk.")
                    existing_df = existing_df[in_window]
                    rows_were_trimmed = True
            existing_years = set(existing_df["year"].unique()) if not existing_df.empty else set()

            forced = "all" in refresh or sport_page in refresh
            wanted = [
                year for year in config.YEARS
                if year >= first_year
                and (school, sport_page, year) not in config.KNOWN_MISSING_SEASONS
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

    if combined:
        config.note(f"{len(combined)} gendered pages are served by a combined page, so their gender label may be loose: " + ", ".join(combined))
    if skipped_empty:
        print(f"{skipped_empty} past seasons had no roster last time and are not asked for again (--retry-empty to check them).")
    if not_discovered:
        shown = ", ".join(not_discovered[:6]) + (", ..." if len(not_discovered) > 6 else "")
        print(f"{len(not_discovered)} school + sport pages were never checked; run `rosters.py discover`: {shown}")
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
                if not maybe_error:
                    state.mark_no_roster(school, sport_page, year)   # a real "no page"; errors are never remembered
            else:
                file_state[t["key"]]["new_fetches"][year] = df
                state.clear_no_roster(school, sport_page, year)
        except Exception as e:
            empty += 1
            print(f"Failed {school} {sport} {year}: {e}")
            scraper.log_scrape(school, sport_page, year, "error", 0)
    state.save_season_status()
    if tasks:
        detail = f", {empty} came back empty" + (f" ({uncertain} after a rate limit or server error; they will be retried next run)" if uncertain else "") if empty else ""
        print(f"[{label}] fetched {len(tasks)} pages{detail}")


def write_files(file_state):
    written = 0
    tidied = {"names": 0, "files": 0, "nameless": 0}
    name_fixes, name_fixes_used = config.load_name_fixes(), set()
    for entry in file_state.values():
        existing_df = entry["existing_df"]
        new_fetches = entry["new_fetches"]

        # A page that suddenly lists far fewer players than before is usually half loaded, not a team that shrank.
        for year, fresh in new_fetches.items():
            before = 0 if existing_df.empty else int((existing_df["year"] == year).sum())
            if before >= 10 and len(fresh) < before / 2:
                print(f"[{entry['school']}] {entry['sport']} {year}: the page now lists {len(fresh)} players but {before} were on disk; check it before publishing.")

        # Freshly fetched years replace what is on disk for that year.
        combined = existing_df
        if not combined.empty and new_fetches:
            combined = combined[~combined["year"].isin(new_fetches.keys())]
        parts = [p for p in [combined, *new_fetches.values()] if not p.empty]

        if not parts:
            print(f"[{entry['school']}] {entry['sport']}: no roster found despite a working slug; worth a manual check.")
            continue

        full_df = pd.concat(parts, ignore_index=True)

        # Every text field written one way (spaces, quotes, invisible characters), and rows with no name dropped, before any comparison below.
        full_df, nameless = scraper.clean_roster_rows(full_df)
        tidied["nameless"] += nameless

        # Names are tidied first: freshly fetched rows still carry jersey numbers, which would hide a stale season from the checks below.
        badges_fixed = scraper.clean_name_badges(full_df)

        # Hand-corrected names (a letter the source page lost); the key is the name as the roster has it, after the clean-up above.
        for (fix_school, wrong), right in name_fixes.items():
            if fix_school == entry["school"] and (full_df["name"] == wrong).any():
                full_df["name"] = full_df["name"].replace(wrong, right)
                name_fixes_used.add((fix_school, wrong))

        # Exact duplicate rows are never real data.
        full_df = full_df.drop_duplicates()

        full_df, dropped = scraper.drop_stale_years(full_df, config.CURRENT_YEAR)
        if dropped:
            print(f"[{entry['school']}] {entry['sport']}: dropped stale year(s) {dropped} (a copy of another year, or last year's seniors not advanced).")
        if full_df.empty:
            print(f"[{entry['school']}] {entry['sport']}: nothing left after dropping stale years; no file written.")
            continue

        if badges_fixed:
            config.note(f"[{entry['school']}] {entry['sport']}: cleaned {badges_fixed} name(s) (badges or extra spaces).")
            tidied["names"] += badges_fixed
            tidied["files"] += 1

        full_df = scraper.sort_roster(full_df)

        full_df.to_csv(entry["output_file"], index=False)
        config.note(f"[{entry['school']}] {entry['sport']}: saved {entry['output_file']}")
        written += 1
    unused = sorted(set(name_fixes) - name_fixes_used)
    if unused:
        print(f"  {len(unused)} name fixes match no athlete in the rosters (a typo in the name, or the page was corrected): "
              + ", ".join(f"{s} {n!r}" for s, n in unused[:5]) + (", ..." if len(unused) > 5 else ""))
    return written, tidied


def pull_all(max_workers=8, refresh=frozenset(), retry_empty=False):
    cache_df = state.load_availability_cache()
    if cache_df.empty:
        print("No availability data found; run `rosters.py discover` first.")
        return

    tasks, file_state = gather_tasks(cache_df, refresh=refresh, retry_empty=retry_empty)
    print(f"{len(tasks)} pages to fetch, {max_workers} schools at a time.")
    for problem in config.name_fix_problems():
        print(f"  name_fixes.csv: {problem}")

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

    state.save_season_status()    # also writes the file on the first run after migrating from the scrape log
    written, tidied = write_files(file_state)
    print(f"Done: {written} roster files written" + (f"; removed jersey numbers from {tidied['names']:,} names in {tidied['files']} of them" if tidied["files"] else "")
          + (f"; dropped {tidied['nameless']} rows with no name" if tidied["nameless"] else "") + ".")


# ───────────────────────── infer ─────────────────────────

# Fills a season whose team page is empty on the site, using the seasons either side.
#
# Who is added to the gap year:
#   1. Athletes on BOTH neighbouring rosters (they must have been on the team in between).
#   2. Juniors on the earlier roster who are not on the later one: almost certainly seniors
#      in the gap year who then graduated.            (ADD_JUNIORS)
#   3. Sophomores on the later roster who are not on the earlier one: almost certainly
#      first-years in the gap year.                   (ADD_SOPHOMORES)
# Not recoverable: anyone on the team only in the gap year, and seniors on the earlier roster
# who took a fifth year. So the filled season is still a minimum, not a full roster.
#
# Safe to rerun. Rows previously added (listed in data/state/inferred_seasons.csv) are replaced;
# if the gap year ever has real rows from the site, the team is left alone.

# (school, sport_page, gap_year, year_before, year_after); years are academic start years
INFERRED_SEASONS = [
    ("Yale", "mens-golf", 2023, 2022, 2024),
]
ADD_JUNIORS = True
ADD_SOPHOMORES = True

LABELS = ["Fy.", "So.", "Jr.", "Sr."]
CLASS_INDEX = {"fy": 0, "fr": 0, "first year": 0, "firstyear": 0, "freshman": 0,
               "so": 1, "sophomore": 1, "jr": 2, "junior": 2, "sr": 3, "senior": 3}
LOG_COLS = ["school", "sport", "year", "name", "rule"]


def class_idx(value):
    return CLASS_INDEX.get(str(value).strip().lower().replace(".", ""))


def find_col(df, options, what):
    for c in options:
        if c in df.columns:
            return c
    raise SystemExit(f"Cannot find the {what} column. Columns are: {list(df.columns)}")


def roster_path(school, sport):
    hits = glob.glob(os.path.join(config.ROSTERS_DIR, "*", f"{sport}_rosters.csv"))
    hits = [h for h in hits if os.path.basename(os.path.dirname(h)).lower() == school.lower()]
    return hits[0] if hits else None


def name_key(name):
    return " ".join(str(scraper.strip_name_badge(name)).lower().split())


def fill(school, sport, gap, before, after, prior):
    """Returns the log rows added, or None if the team was skipped."""
    path = roster_path(school, sport)
    if not path:
        print(f"{school} {sport}: no roster file found, skipped")
        return None
    df = pd.read_csv(path)
    year_c = find_col(df, ["year"], "year")
    name_c = find_col(df, ["name"], "name")
    class_c = find_col(df, ["class", "cl", "academic_year", "class_year"], "class")

    mine = prior[(prior["school"] == school) & (prior["sport"] == sport) & (prior["year"] == gap)]
    ours = {name_key(n) for n in mine["name"]}
    existing = df[df[year_c] == gap]
    if len(existing):
        real = existing[~existing[name_c].map(name_key).isin(ours)]
        if len(real):
            print(f"{school} {sport} {gap}: has {len(real)} rows from the site, left alone")
            return None
        df = df[df[year_c] != gap]            # rebuild our own earlier fill

    b, a = df[df[year_c] == before].copy(), df[df[year_c] == after].copy()
    if b.empty or a.empty:
        print(f"{school} {sport}: need both {before} and {after} rosters, skipped")
        return None
    b["_k"], a["_k"] = b[name_c].map(name_key), a[name_c].map(name_key)
    b, a = b.drop_duplicates("_k"), a.drop_duplicates("_k")
    in_b, in_a = set(b["_k"]), set(a["_k"])

    added = []                                 # (row, class label, rule)
    for _, r in a.iterrows():                  # on both, or a sophomore who is new
        i = class_idx(r[class_c])
        if r["_k"] in in_b:
            before_i = class_idx(b.loc[b["_k"] == r["_k"], class_c].iloc[0])
            lab = LABELS[i - 1] if i else (LABELS[before_i + 1] if before_i is not None and before_i < 3 else "")
            added.append((r, lab, "both"))
        elif ADD_SOPHOMORES and i == 1:
            added.append((r, LABELS[0], "sophomore"))
    for _, r in b.iterrows():                  # a junior who then graduated
        if r["_k"] not in in_a and ADD_JUNIORS and class_idx(r[class_c]) == 2:
            added.append((r, LABELS[3], "junior"))

    rows = []
    for r, lab, rule in added:
        row = r.drop(labels="_k").copy()
        row[class_c], row[year_c] = lab, gap
        rows.append(row)
    out = pd.DataFrame(rows)
    pd.concat([df, out], ignore_index=True).to_csv(path, index=False)

    counts = pd.Series([rule for _, _, rule in added]).value_counts().to_dict()
    print(f"{school} {sport} {gap}: {len(out)} athletes ({counts}) from {len(b)} on the {before} roster "
          f"and {len(a)} on the {after} roster")
    return [{"school": school, "sport": sport, "year": gap, "name": r[name_c], "rule": rule}
            for r, _, rule in added]


def infer_all():
    log_path = config.INFERRED_LOG_PATH
    prior = pd.read_csv(log_path) if os.path.exists(log_path) else pd.DataFrame(columns=LOG_COLS)
    for c in LOG_COLS:
        if c not in prior.columns:
            prior[c] = ""
    result = prior.copy()
    for school, sport, gap, before, after in INFERRED_SEASONS:
        new = fill(school, sport, gap, before, after, prior)
        if new is not None:
            same = (result["school"] == school) & (result["sport"] == sport) & (result["year"] == gap)
            result = pd.concat([result[~same], pd.DataFrame(new, columns=LOG_COLS)], ignore_index=True)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    result.sort_values(["school", "sport", "year", "name"], kind="stable").to_csv(log_path, index=False)
    print(f"Added athletes are listed in {log_path}")


def main():
    parser = argparse.ArgumentParser(description="The roster steps: discover, pull, infer.")
    sub = parser.add_subparsers(dest="command", required=True)

    d = sub.add_parser("discover", help="Discover which school+sport roster pages exist and which slug works for each.")
    d.add_argument("--recheck", metavar="SCHOOL,SPORT_PAGE", default=None,
                   help="Diagnose a single combo instead of running full discovery. Re-checks just this (school, sport_page) with full "
                        "verbose URL/status/selector logging, e.g. --recheck Harvard,rowing. Always single-threaded regardless of --workers.")
    d.add_argument("--workers", type=int, default=8, help="Number of schools to discover concurrently (default: 8, one per school).")
    d.add_argument("--verbose-sport", metavar="SPORT_PAGE", default=None,
                   help="Run a REAL full discovery (all schools, multi-threaded, as normal) but with full URL/status/selector logging for "
                        "every school's attempt at this one sport_page. For diagnosing a combo that only fails inside a full run and can't be "
                        "reproduced via an isolated --recheck, e.g. --verbose-sport rowing")
    d.add_argument("-v", "--verbose", action="store_true", help="Print a line for every school + sport checked.")

    p = sub.add_parser("pull", help="Pull every year of roster data for every school+sport confirmed available by discover.")
    p.add_argument("--workers", type=int, default=8, help="Number of schools to pull concurrently (default: 8, one per school).")
    p.add_argument("--refresh", metavar="SPORT_PAGE[,SPORT_PAGE...]|all", default="",
                   help="Ignore data already on disk for these sport_pages (or 'all') and refetch every year. Use after a fix that changes "
                        "how past years are fetched or labeled, e.g. --refresh mens-rowing,rowing,womens-rowing,womens-lightweight-rowing")
    p.add_argument("-v", "--verbose", action="store_true", help="Print a line for every skipped sport, page fetched and file saved.")
    p.add_argument("--retry-empty", action="store_true", help="Ask again for past seasons that had no roster last time.")

    sub.add_parser("infer", help="Fill seasons whose team page is blank (INFERRED_SEASONS) from the seasons either side.")

    args = parser.parse_args()
    config.VERBOSE = getattr(args, "verbose", False)
    if args.command == "discover":
        if args.recheck:
            school, sport_page = [s.strip() for s in args.recheck.split(",", 1)]
            recheck_combo(school, sport_page)
        else:
            discover_all(max_workers=args.workers, verbose_sport_page=args.verbose_sport)
    elif args.command == "pull":
        refresh = frozenset(x.strip() for x in args.refresh.split(",") if x.strip())
        pull_all(max_workers=args.workers, refresh=refresh, retry_empty=args.retry_empty)
    else:
        infer_all()


if __name__ == "__main__":
    main()
