"""checks.py — read-only diagnostics.

    python checks.py audit                                 roster sizes, class-advancement and long-career checks (saved to data/audit/audit_report.txt)
    python checks.py probe SCHOOL SPORT_PAGE YEARS         compare the roster URL forms across seasons for one team, to find which form serves a real roster
    python checks.py places [--forget]                     find hometowns whose dot is not where the hometown says it is
    python checks.py code                                  check the scripts themselves (run automatically before run_pipeline.py)
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

import pandas as pd

import config
import scraper
import state
from geocode_rosters import PLACE_KM_LIMIT, misplaced_hometowns
from run_pipeline import find_code_problems


# ───────────────────────────── audit: class labels, coverage, long careers ─────────────────────────────

OUT_PATH = os.path.join(config.AUDIT_DIR, "class_audit.csv")
REPORT_PATH = os.path.join(config.AUDIT_DIR, "audit_report.txt")


# Copies everything printed to the report file as well as the terminal.
class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for s in self.streams:
            s.write(text)

    def flush(self):
        for s in self.streams:
            s.flush()


def load_rosters():
    frames = []
    for path in glob.glob(os.path.join(config.ROSTERS_DIR, "*", "*_rosters.csv")):
        df = pd.read_csv(path).reindex(columns=["name", "class", "hometown", "year"])
        df["school"] = os.path.basename(os.path.dirname(path)).title()
        df["sport"] = os.path.basename(path)[:-len("_rosters.csv")]
        frames.append(df)
    if not frames:
        raise FileNotFoundError(f"No roster files found in {config.ROSTERS_DIR}")
    df = pd.concat(frames, ignore_index=True)
    df["name"] = df["name"].astype(str).str.split().str.join(" ")
    df["rank"] = df["class"].map(scraper.class_rank)
    return df


# One row per (school, sport, year): size, grad-labeled count, and how many of last year's seniors reappear.
def audit(df):
    rows = []
    for (school, sport), g in df.groupby(["school", "sport"]):
        by_year = {int(y): sub for y, sub in g.groupby("year")}
        for y, cur in sorted(by_year.items()):
            prev = by_year.get(y - 1)
            seniors = set(prev.loc[prev["rank"] == 4, "name"]) if prev is not None else set()
            back = seniors & set(cur["name"])
            rows.append({
                "school": school, "sport": sport, "year": y, "roster_size": len(cur),
                "grad_labeled": int((cur["rank"] == 5).sum()),
                "prior_seniors": len(seniors), "seniors_returning": len(back),
                "pct_returning": round(100 * len(back) / len(seniors)) if seniors else None,
            })
    return pd.DataFrame(rows)


# Class labels by year for athletes with 5+ seasons in one sport, flagging any label that failed to advance year to year.
def long_careers(df, limit=4):
    rows = []
    for (school, sport, name, _), g in df.groupby(["school", "sport", "name", "hometown"], dropna=False):
        g = g.sort_values("year").drop_duplicates("year")
        if len(g) <= limit:
            continue
        ranks = list(g["rank"])
        stuck = any(a is not None and a == b and not pd.isna(a) and not pd.isna(b) for a, b in zip(ranks, ranks[1:]))
        rows.append({
            "school": school, "sport": sport, "name": name, "first": int(g["year"].iloc[0]), "last": int(g["year"].iloc[-1]),
            "classes": " ".join(f"{int(y) % 100}:{c}" for y, c in zip(g["year"], g["class"])), "label_not_advanced": stuck,
        })
    return pd.DataFrame(rows)


# Roster rows per school and season (a COVID dip shows up here), plus teams missing a season between two seasons that exist.
def coverage(df):
    table = df.groupby(["school", "year"]).size().unstack(fill_value=0)
    table.to_csv(os.path.join(config.AUDIT_DIR, "coverage.csv"))
    print("\nRoster rows per school and season (2016 = 2016-17):")
    print(table.to_string())

    gaps = []
    for (school, sport), g in df.groupby(["school", "sport"]):
        have = set(g["year"].astype(int))
        missing = [y for y in range(min(have), max(have) + 1) if y not in have and (school, sport, y) not in config.KNOWN_MISSING_SEASONS]
        if missing:
            gaps.append((school, sport, missing))
    covid = [g for g in gaps if g[2] == [2020]]
    other = [g for g in gaps if g[2] != [2020]]
    print(f"\n{len(covid)} teams have no 2020-21 roster but do have seasons on both sides of it (expected after COVID).")
    print(f"{len(other)} teams are missing seasons between ones that exist, other than the known-missing ones in site_rules.csv" + (":" if other else "."))
    for school, sport, missing in other[:25]:
        print(f"  {school} {sport}: missing {missing}")
    if len(other) > 25:
        print(f"  ... and {len(other) - 25} more")


# Players on two consecutive rosters should have moved up exactly one class; a season where most did not is probably copied, shifted by a year or mislabeled.
def class_advancement(df, min_returning=6, threshold=0.5):
    rows = []
    known = df[df["rank"].between(1, 4)]
    for (school, sport), g in known.groupby(["school", "sport"]):
        ranks = {int(y): sub.drop_duplicates("name").set_index("name")["rank"] for y, sub in g.groupby("year")}
        for year, current in sorted(ranks.items()):
            previous = ranks.get(year - 1)
            if previous is None:
                continue
            both = current.index.intersection(previous.index)
            if len(both) < min_returning:
                continue
            change = current[both] - previous[both]
            moved = int((change == 1).sum())
            rows.append({"school": school, "sport": sport, "year": year, "returning": len(both), "same": int((change == 0).sum()),
                         "moved_up_one": moved, "plus_two": int((change == 2).sum()), "pct": round(100 * moved / len(both))})
    out = pd.DataFrame(rows)
    if out.empty:
        return
    out.to_csv(os.path.join(config.AUDIT_DIR, "class_advancement.csv"), index=False)
    bad = out[out["pct"] < 100 * threshold].sort_values("pct")
    print(f"\nSeasons where under {threshold:.0%} of returning players moved up one class ({len(bad)} of {len(out)}; 2021 is expected after COVID).\nOne odd season shows up twice in a row (it is the season both rows share). same = class unchanged (stale labels or a skipped COVID year); plus_two = labels catching up after COVID:")
    print(bad.head(30).to_string(index=False) if len(bad) else "  none")
    if len(bad) > 30:
        print(f"  ... and {len(bad) - 30} more in data/audit/class_advancement.csv")


def _run_audit():
    df = load_rosters()
    out = audit(df)
    out.to_csv(OUT_PATH, index=False)
    print(f"{len(out)} school/sport/year rosters checked; full table in {OUT_PATH}\n")

    coverage(df)
    class_advancement(df)

    # Seniors returning en masse is the carry-over signature; real returns (5th years) are a small share.
    sus = out[(out["prior_seniors"] >= 3) & (out["pct_returning"] >= 50)].sort_values("pct_returning", ascending=False)
    print(f"Rosters where 50%+ of last year's seniors reappear ({len(sus)}):")
    print(sus.to_string(index=False) if len(sus) else "  none")

    print("\nGrad-labeled athletes by year (all schools/sports):")
    print(out.groupby("year")[["grad_labeled"]].sum().join(out[out["grad_labeled"] > 0].groupby("year").size().rename("rosters_with_any")).fillna(0).astype(int).to_string())

    print("\nRosters with the most grad-labeled athletes:")
    print(out.sort_values("grad_labeled", ascending=False).head(10)[["school", "sport", "year", "grad_labeled", "roster_size"]].to_string(index=False))

    lc = long_careers(df)
    if len(lc):
        lc.sort_values(["label_not_advanced", "school", "sport"], ascending=[False, True, True]).to_csv(
            os.path.join(config.AUDIT_DIR, "long_careers.csv"), index=False)
        stuck = lc[lc["label_not_advanced"]]
        print(f"\n{len(lc)} athletes have 5+ seasons in one sport; {len(stuck)} have a class label that did not advance:")
        print(stuck.head(30).to_string(index=False) if len(stuck) else "  none")
        print("Full list: data/audit/long_careers.csv")

    unknown = df[df["rank"].isna() & df["class"].notna()]["class"].value_counts().head(10)
    if len(unknown):
        print("\nClass labels not recognized (top 10):")
        print(unknown.to_string())


def audit_main():
    os.makedirs(config.AUDIT_DIR, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as report:
        report.write(f"Audit run {datetime.datetime.now():%Y-%m-%d %H:%M}\n\n")
        terminal = sys.stdout
        sys.stdout = Tee(terminal, report)
        try:
            _run_audit()
        finally:
            sys.stdout = terminal
    print(f"\nReport saved to {REPORT_PATH}")


# ───────────────────────────── probe: which URL form has a real roster, by season ─────────────────────────────

HEADERS = {"User-Agent": "Mozilla/5.0"}
SENTINEL_YEAR = 2099  # a season that cannot exist: whatever page comes back is the site's default


def parse_years(text):
    if re.fullmatch(r"\d{4}-\d{4}", text):
        a, b = map(int, text.split("-"))
        return list(range(a, b + 1))
    return [int(y) for y in text.split(",")]


def form_of(url):
    if re.search(r"/\d{4}-\d{2}$", url):
        return "dash"
    return "bare" if re.search(r"/\d{4}$", url) else "live"


# Fetches one URL on its own: (cleaned set of player names, a note when there is no roster such as "HTTP 404", {name: class rank}).
def fetch(url, base, sport, slug, year, school):
    status = scraper._get_with_retry(url, headers=HEADERS).status_code
    if status != 200:
        return frozenset(), f"HTTP {status}", {}
    original = scraper._url_candidates
    scraper._url_candidates = lambda *a: [url]
    try:
        df, _ = scraper.scrape_roster(base, sport, [slug], year=year, school=school)
    finally:
        scraper._url_candidates = original
    if df.empty:
        return frozenset(), "page has no players", {}
    clean = [scraper.strip_name_badge(n) for n in df["name"]]
    ranks = {n: scraper.class_rank(c) for n, c in zip(clean, df["class"])} if "class" in df.columns else {}
    return frozenset(clean), "", ranks


def overlap(a, b):
    return len(a & b) / max(min(len(a), len(b)), 1)


def probe_main(args):
    base = config.bases_df.set_index("school").loc[args.school, "site_page"]
    row = state.get_cached_row(state.load_availability_cache(), args.school, args.sport_page)
    if row is None:
        print(f"Note: '{args.sport_page}' is not a sport page in the availability file for {args.school}; "
              f"check the exact name with: grep -i {args.sport_page.split('-')[-1]} data/state/school_sport_state.csv")
    slug = args.slug or (row["resolved_slug"] if row is not None and row["available"] == True else args.sport_page)
    slug_for = lambda year: slug
    years = parse_years(args.years)

    found = {}
    for year in years:
        year_slug = slug_for(year)
        urls = list(scraper.url_forms(base, args.sport_page, year_slug, year))
        if year == config.CURRENT_YEAR:
            urls = urls + [f"https://{base}/sports/{year_slug}/roster"]
        for url in urls:
            found.setdefault(form_of(url), {})[year] = fetch(url, base, args.sport_page, year_slug, year, args.school)
    forms = [f for f in ("bare", "dash", "live") if f in found]

    default = {}
    for url in scraper.url_forms(base, args.sport_page, slug_for(years[-1]), SENTINEL_YEAR):
        default[form_of(url)] = fetch(url, base, args.sport_page, slug_for(years[-1]), SENTINEL_YEAR, args.school)[0]
    is_default = lambda form, y: bool(found.get(form, {}).get(y, (frozenset(),))[0]) and found[form][y][0] == default.get(form) and y != config.CURRENT_YEAR

    # Same roster = same letter, in any column.
    letters = {}
    def label(form, year):
        if year not in found[form]:
            return "-"
        names, note = found[form][year][:2]
        if not names:
            return note
        return f"{len(names)} players ({letters.setdefault(names, chr(ord('A') + len(letters)))})" + (" DEFAULT" if is_default(form, year) else "")

    shown = {"bare": "bare-year URL", "dash": "dash URL", "live": "live /roster"}
    print(f"\n{args.school} {args.sport_page} (slug '{slug}'). Same letter = identical roster; "
          f"DEFAULT = the page the site returns for a season that does not exist (so no real roster).\n")
    print(f"{'season':<9}" + "".join(f"{shown[f]:<26}" for f in forms))
    for year in years:
        print(f"{str(year) + '-' + str(year + 1)[-2:]:<9}" + "".join(f"{label(f, year):<26}" for f in forms))

    def names_of(form, year):
        return found.get(form, {}).get(year, (frozenset(), ""))[0]

    real = lambda form, y: names_of(form, y) if not is_default(form, y) else frozenset()
    both = [y for y in years if real("bare", y) and real("dash", y)]
    agree = [y for y in both if real("bare", y) == real("dash", y)]
    shifted = [f"bare {a}-{str(a + 1)[-2:]} shows the dash {b}-{str(b + 1)[-2:]} roster" for a in years for b in years
               if a != b and real("bare", a) and real("bare", a) == real("dash", b)]
    print(f"\nThe bare-year and dash URLs show the same roster in {len(agree)} of {len(both)} seasons where both have a page.")
    if shifted:
        print("Offsets: " + "; ".join(shifted) + ".")
    if "live" in found and names_of("live", config.CURRENT_YEAR):
        same_as = [y for y in years if y != config.CURRENT_YEAR and names_of("dash", y) == names_of("live", config.CURRENT_YEAR) or
                   y != config.CURRENT_YEAR and names_of("bare", y) == names_of("live", config.CURRENT_YEAR)]
        if same_as:
            print(f"The live page matches the roster of {', '.join(map(str, same_as))}: the current roster may be stale.")

    # Real rosters move their players up one class a year; unchanged labels mean stale data, +2 is the COVID catch-up.
    print("\nClass changes vs the previous season (share of returning players; a real roster is mostly +1):")
    shown = False
    plus1 = {}
    for y in years[1:]:
        for form in ("bare", "dash"):
            if not real(form, y):
                continue
            best = None
            for prev_form in ("bare", "dash"):
                if y - 1 in found.get(prev_form, {}) and real(prev_form, y - 1):
                    prev = found[prev_form][y - 1][2]
                    common = [n for n in found[form][y][2] if found[form][y][2][n] and prev.get(n)]
                    if best is None or len(common) > len(best[1]):
                        best = (prev_form, common, prev)
            if best and len(best[1]) >= 5:
                changes = [found[form][y][2][n] - best[2][n] for n in best[1]]
                share = lambda k: f"{sum(1 for c in changes if c == k) / len(changes):.0%}"
                print(f"  {y}-{str(y + 1)[-2:]} {form:<4} vs {best[0]:<4} {y - 1}-{str(y)[-2:]}: same {share(0):>4} | +1 {share(1):>4} | +2 {share(2):>4}  ({len(changes)} players)")
                shown = True
                plus1[(form, y)] = sum(1 for c in changes if c == 1) / len(changes)
    if not shown:
        print("  (not enough players in common to tell)")

    print()
    for form in ("bare", "dash"):
        if form not in found:
            continue
        defaults = [y for y in years if is_default(form, y)]
        present = [y for y in years if real(form, y)]
        same = {}
        for y in present:
            same.setdefault(names_of(form, y), []).append(y)
        repeats = [ys for ys in same.values() if len(ys) > 1]
        links = [overlap(names_of(form, a), names_of(form, b)) for a, b in zip(present, present[1:]) if b == a + 1]
        average = sum(links) / len(links) if links else None
        note = ("identical across seasons: " + "; ".join("=".join(map(str, ys)) for ys in repeats)) if repeats else "a different roster each season"
        carry = f", {average:.0%} of players carry over between consecutive seasons" if average is not None else ""
        fallback = f"; {', '.join(map(str, defaults))} just show the default page" if defaults else ""
        print(f"{form:>4}: {len(present)} of {len(years)} seasons have a real page; {note}{carry}{fallback}")

    ruled = (args.school, args.sport_page) in config.SCHOOL_FORM_THROUGH or (args.school, args.sport_page) in config.SCHOOL_FORM_FROM
    rule_pick = {y: form_of(scraper._url_candidates(base, args.sport_page, slug, y, args.school)[0]) for y in years}
    usable = {y: [f for f in ("bare", "dash") if real(f, y)] for y in years}
    summary = {y: ("none" if not u else "+".join(u) + (" (they differ)" if len(u) == 2 and real("bare", y) != real("dash", y) else "")) for y, u in usable.items()}
    print("\nURL form with a real roster, by season: " + "; ".join(f"{y}-{str(y + 1)[-2:]}: {summary[y]}" for y in years))

    # One form per season: the only real one, or, when both are real, the one whose classes move up (a stale page keeps last year's labels).
    pick = {}
    for y in years:
        if len(usable[y]) == 1:
            pick[y] = usable[y][0]
        elif len(usable[y]) == 2:
            a, b = plus1.get(("bare", y)), plus1.get(("dash", y))
            if a is not None and b is not None and abs(a - b) >= 0.3:
                pick[y] = "bare" if a > b else "dash"
    unresolved = [y for y in years if usable[y] and y not in pick]
    runs = []
    for y in sorted(pick):
        if runs and runs[-1][1] == pick[y] and runs[-1][0] == y - 1:
            runs[-1][0] = y
        else:
            runs.append([y, pick[y]])

    print()
    if ruled:
        good = [y for y in years if rule_pick[y] == pick.get(y) or (y not in pick and rule_pick[y] in usable[y])]
        bad = [y for y in years if y not in good]
        if not bad:
            print(f"The form rule in site_rules.csv (the form_through / form_from rows) matches the right URL in all {len(years)} seasons shown. Nothing to change.")
        else:
            print(f"The form rule in site_rules.csv (form_through / form_from) disagrees with the evidence for {', '.join(f'{y}-{str(y + 1)[-2:]}' for y in bad)}.")
    if runs and not (ruled and not bad):
        print("Suggested rows for data/inputs/site_rules.csv (school,sport_page,rule,year,value,note):")
        for k, (through, form) in enumerate(runs):
            if k == len(runs) - 1 and years[-1] == config.CURRENT_YEAR:      # the run that reaches the current season carries on from here
                start = runs[k - 1][0] + 1 if k else years[0]
                print(f"  {args.school},{args.sport_page},form_from,{start},{form},")
            else:
                print(f"  {args.school},{args.sport_page},form_through,{through},{form},")
        print("It covers the seasons probed; seasons before the first probed year use the first form, and you can raise the last year once you have probed later ones.")
    elif not runs:
        print("No season has a real roster at this slug. Seasons that show an HTTP error have no page here (try --slug with another spelling).")
    if unresolved and not (ruled and not bad):
        print(f"Both forms look real for {', '.join(f'{y}-{str(y + 1)[-2:]}' for y in unresolved)} and the class changes don't settle it; compare those pages by eye.")


# ───────────────────────────── places: is each dot where its hometown says? ─────────────────────────────
# Checks every hometown in docs/data.json against the US state outlines in docs/us-states.json, and writes the suspicious ones to
# data/audit/place_review.csv. Two kinds: a hometown with a US state whose dot is more than PLACE_KM_LIMIT km outside that state
# (the geocoder read an abbreviation as another country), and a hometown with no US state whose dot is inside the US.
# --forget removes the flagged points from hometown_cache.csv so the next `geocode_rosters.py` looks them up again.
def places_main(forget=False):
    data_path = os.path.join(config.DOCS_DIR, "data.json")
    if not os.path.exists(data_path):
        sys.exit(f"Not found: {data_path}")
    with open(data_path) as f:
        d = json.load(f)
    rows = misplaced_hometowns(d)
    if rows is None:
        sys.exit(f"Not found: {os.path.join(config.DOCS_DIR, 'us-states.json')} (run prepare_states.py once to create it)")
    total = len(set(d["hometown"]))
    os.makedirs(config.AUDIT_DIR, exist_ok=True)
    out = os.path.join(config.AUDIT_DIR, "place_review.csv")
    pd.DataFrame(rows, columns=["hometown", "athletes", "labelled_as", "dot_is_in", "km_from_labelled_state", "lat", "lng", "suggested_fix_check_it"]).to_csv(out, index=False)

    far = [r for r in rows if r[2] != "International / Other"]
    inside = [r for r in rows if r[2] == "International / Other"]
    print(f"{total:,} hometowns checked against the state outlines.")
    print(f"  {len(far)} have a US state but the dot is more than {PLACE_KM_LIMIT} km outside it ({sum(r[1] for r in far)} athletes)")
    print(f"  {len(inside)} have no US state but the dot is inside the US ({sum(r[1] for r in inside)} athletes)")
    for r in rows[:15]:
        where = r[3] if r[3] == "outside the US" else f"in {r[3]}"
        print(f"    {r[0]!r:38} labelled {r[2]:<22} dot {where:<15} x{r[1]}")
    print(f"Full list: {out}")
    if not rows:
        return
    if not forget:
        print("Fix the hometown text in data/inputs/hometown_fixes.csv where it is a typo, and run again with --forget to look the rest up again.")
        return
    cache = pd.read_csv(config.HOMETOWN_CACHE_PATH)
    flagged = {(round(r[5], 4), round(r[6], 4)) for r in rows}
    drop = [(round(a, 4), round(b, 4)) in flagged for a, b in zip(cache["latitude"], cache["longitude"])]
    cache[[not x for x in drop]].to_csv(config.HOMETOWN_CACHE_PATH, index=False)
    print(f"Removed {sum(drop)} cache rows. Now run: python geocode_rosters.py && python build_data_json.py")


def code_main():
    folder = os.path.dirname(os.path.abspath(__file__))
    problems = find_code_problems(folder)
    count = len([f for f in os.listdir(folder) if f.endswith(".py")])
    print("\n".join(problems) if problems else f"No problems found in the {count} scripts.")
    sys.exit(1 if problems else 0)


def main():
    parser = argparse.ArgumentParser(description="Read-only diagnostics: audit the roster files, probe one team's URL forms, or check where the dots are.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit", help="Check the roster files; the report is also saved to data/audit/audit_report.txt.")
    p = sub.add_parser("probe", help="Compare roster URL forms across seasons, e.g.: checks.py probe Yale mens-soccer 2016-2020")
    p.add_argument("school")
    p.add_argument("sport_page")
    p.add_argument("years", help="One season (2017), a range (2016-2020) or a list (2016,2018,2020); a season is the year it starts.")
    p.add_argument("--slug", help="Try this URL slug instead of the one discovery found, e.g. --slug womens-volleyball")
    p = sub.add_parser("places", help="Find hometowns whose dot is not where the hometown says it is (writes data/audit/place_review.csv).")
    p.add_argument("--forget", action="store_true", help="Also remove the flagged points from hometown_cache.csv so the next geocode run looks them up again.")
    sub.add_parser("code", help="Check the scripts themselves for mixed-up versions, duplicate definitions and typos.")
    args = parser.parse_args()
    if args.command == "code":
        code_main()
    elif args.command == "audit":
        audit_main()
    elif args.command == "probe":
        probe_main(args)
    elif args.command == "places":
        places_main(args.forget)


if __name__ == "__main__":
    main()
