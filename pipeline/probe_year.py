"""probe_year.py — compares the roster URL forms across seasons for one team, to find which form serves a different roster per season."""
import argparse
import re

import availability
import config
import roster_data
from roster_data import class_rank
import scraper
from site_rules import SCHOOL_FORM_THROUGH

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
    clean = [roster_data.strip_name_badge(n) for n in df["name"]]
    ranks = {n: class_rank(c) for n, c in zip(clean, df["class"])} if "class" in df.columns else {}
    return frozenset(clean), "", ranks


def overlap(a, b):
    return len(a & b) / max(min(len(a), len(b)), 1)


def main():
    parser = argparse.ArgumentParser(description="Compare roster URL forms across seasons, e.g.: probe_year.py Yale mens-soccer 2016-2020")
    parser.add_argument("school")
    parser.add_argument("sport_page")
    parser.add_argument("years", help="One season (2017), a range (2016-2020) or a list (2016,2018,2020); a season is the year it starts.")
    parser.add_argument("--slug", help="Try this URL slug instead of the one discovery found, e.g. --slug womens-volleyball")
    args = parser.parse_args()

    base = config.bases_df.set_index("school").loc[args.school, "site_page"]
    row = availability.get_cached_row(availability.load_availability_cache(), args.school, args.sport_page)
    if row is None:
        print(f"Note: '{args.sport_page}' is not a sport page in the availability file for {args.school}; "
              f"check the exact name with: grep -i {args.sport_page.split('-')[-1]} data/_school_sport_availability.csv")
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
    for url in scraper._url_candidates(base, args.sport_page, slug_for(years[-1]), SENTINEL_YEAR, args.school):
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

    ruled = (args.school, args.sport_page) in SCHOOL_FORM_THROUGH
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
            print(f"The form rule in site_rules.py (SCHOOL_FORM_THROUGH) matches the right URL in all {len(years)} seasons shown. Nothing to change.")
        else:
            print(f"The form rule in site_rules.py disagrees with the evidence for {', '.join(f'{y}-{str(y + 1)[-2:]}' for y in bad)}.")
    if runs and not (ruled and not bad):
        rule = ", ".join(f"({through}, '{form}')" for through, form in runs)
        print(f"Suggested rule from these seasons: ('{args.school}', '{args.sport_page}'): [{rule}] in SCHOOL_FORM_THROUGH (pipeline/site_rules.py).")
        print("It covers the seasons probed; seasons before the first probed year use the first form, and you can raise the last year once you have probed later ones.")
    elif not runs:
        print("No season has a real roster at this slug. Seasons that show an HTTP error have no page here (try --slug with another spelling).")
    if unresolved and not (ruled and not bad):
        print(f"Both forms look real for {', '.join(f'{y}-{str(y + 1)[-2:]}' for y in unresolved)} and the class changes don't settle it; compare those pages by eye.")


if __name__ == "__main__":
    main()
