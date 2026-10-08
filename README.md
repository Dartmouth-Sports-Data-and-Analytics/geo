# Ivy League Athletics Roster Map

An interactive map of where Ivy League varsity athletes come from. A Python pipeline scrapes every roster page on the eight schools' athletics sites, geocodes each hometown, and writes one JSON file that a static Leaflet page draws as one dot per athlete.

**Live site:** `https://dartmouth-sports-data-and-analytics.github.io/geo/`

This file is the short version. `PROJECT_REFERENCE.md` explains how the pipeline works and why.

## What the map does

- One dot per person, colored by school; clicking it shows their sports, seasons and hometown.
- Filter by school, season, sport and region, and search by name.
- A separate **State heat maps** page shows all eight schools at once, each a US map shaded by players per state; hover a state for its player count and sports.
- A person on two teams (for example coed and women's sailing) is one dot with both listed.
- "Year" always means the academic year: 2024 is the 2024–25 season, for every sport. Seasons run from 2016–17 to the current one; 2020–21 is thin or missing at many schools because of COVID. Both pages open on the latest five seasons, with a Latest 5 button for a quick change (Select all shows every season).

## Structure

```
pipeline/   Python scripts (code only)
data/       inputs/ (hand-edited), state/ (kept by the pipeline), audit/ (generated checks), rosters/ and geo-rosters/
docs/       The website, published by GitHub Pages
```

| Path | What it is |
|---|---|
| `pipeline/run_pipeline.py` | Runs the five steps in order (discover, pull, infer, geocode, build); first checks that the packages are installed and the scripts are consistent |
| `pipeline/rosters.py` | The three roster steps as subcommands: `discover` (finds which school + sport pages exist and the working URL), `pull` (downloads each season's roster, drops stale or copied seasons) and `infer` (rebuilds a season whose team page is blank, Yale men's golf 2023-24, from the seasons either side; lists who it added in `data/state/inferred_seasons.csv`) |
| `pipeline/geocode_rosters.py` | Turns hometowns into coordinates (OpenStreetMap Nominatim), with a cache |
| `pipeline/build_data_json.py` | Merges everything into `docs/data.json`, one entry per athlete per sport and run of seasons, then stamps the `?v=` suffixes in `docs/` |
| `pipeline/config.py` | Paths, school list and seasons (the current season comes from today's date), and the loader for `data/inputs/site_rules.csv` |
| `pipeline/scraper.py` | Fetching and parsing roster pages with polite rate limiting, plus the roster-table cleanup and stale-season checks |
| `pipeline/state.py` | What the pipeline remembers between runs: which school + sport pages exist, and past seasons known to have no roster |
| `pipeline/checks.py` | Read-only checks: `audit` (suspicious rosters), `probe` (compare the two roster URL forms for one team), `places` (is each dot where its hometown says) and `code` (are the scripts consistent) |
| `pipeline/prepare_states.py` | One-time: downloads US state outlines and fixes them for the heat maps |
| `requirements.txt` | Python packages the pipeline needs |
| `data/inputs/sport_page_reference.xlsx` | Hand-edited list of sports and URL slugs |
| `data/inputs/site_rules.csv` | Hand-confirmed quirks of each school's site (URL patterns, missing seasons, unavailable teams), one row each with a note; edit this file, not the code |
| `data/inputs/hometown_fixes.csv`, `name_fixes.csv`, `regions.csv` | Hand-edited corrections for misspelled hometowns and for names the source got wrong; which US states belong to which region |
| `data/rosters/`, `data/geo-rosters/` | Scraped rosters, and the same with coordinates added |
| `data/state/` | Pipeline state: availability, season status, hometown cache, failed lookups, inferred seasons, scrape log |
| `data/audit/` | Output of `checks.py` |
| `docs/index.html`, `app.js`, `style.css` | The roster map |
| `docs/shared.js`, `shared.css` | Constants and helpers (school colors, formatting, chips) and base styles used by every page |
| `docs/heatmaps.html`, `heatmaps.js`, `heatmaps.css` | The eight state heat maps (uses d3) |
| `docs/about.html`, `about-heatmaps.html`, `about.css` | The "About the data" page for each map |
| `docs/data.json`, `docs/us-states.json` | Built roster data; US state outlines (downloaded once, see below) |

## Running it

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cd pipeline
python run_pipeline.py                         # discover -> pull -> infer -> geocode -> build
python run_pipeline.py --only pull             # one step
python run_pipeline.py --refresh baseball      # refetch every season of a sport
python geocode_rosters.py --retry-failed       # retry hometowns with no match
python run_pipeline.py -v                      # also print every skipped sport and fetched page
python rosters.py pull --retry-empty           # ask again for past seasons that had no roster last time
python checks.py audit                         # checks, including roster rows per school and season
python checks.py probe Yale mens-soccer 2016-2020  # compare both URL forms across seasons for one team
python checks.py probe Yale volleyball 2016-2019 --slug womens-volleyball  # try another URL spelling
python checks.py places                        # list dots that are far from the state in their hometown
python build_data_json.py --check-regions      # list hometowns that fell into International / Other

cd ../docs
python3 -m http.server 8000                    # preview at http://localhost:8000
```

The heat maps need US state outlines once: `python prepare_states.py` (from `pipeline/`) downloads them, fixes their polygon winding, and writes `docs/us-states.json`.

Cache-busting `?v=` suffixes in `docs/` are content hashes, stamped automatically at the end of `build_data_json.py`. After editing anything in `docs/` by hand, run `python build_data_json.py --stamp-only` (or install the pre-commit hook below) so browsers fetch the new files. To stamp automatically on every commit, install this hook once from the repo root:

```bash
printf '#!/bin/sh\npython3 pipeline/build_data_json.py --stamp-only && git add docs\n' > .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

To publish: commit and push `docs/data.json`. In **Settings → Pages**, deploy from branch `main`, folder `/docs`.

## Notes

- **Basemap** is Stadia Maps' Alidade Smooth vector style, drawn with MapLibre GL inside Leaflet, with place labels in English. It works without signup on `localhost`. On a `github.io` address, add the domain in your Stadia dashboard (or use an API key). If the vector style cannot load, the map falls back to Stadia's raster tiles, which use local place names.
- **Data quality:** hometowns are shown as the schools publish them, and a few cannot be geocoded, so those athletes have no dot. Athletes can appear for five seasons when a school lists a fifth year.
- **Players only:** roster pages can also list coaches, managers and support staff, so `scraper.py` skips any card whose profile link has a `/coaches/` or `/staff/` segment or whose position or title reads like a staff title (coach, manager, strength, trainer, director, administrator and similar). A real athlete listed under such a title would be dropped too. If a staff member still appears, add their title or link path to `STAFF_TITLE` or `STAFF_LINK` in `scraper.py` and run `python run_pipeline.py --refresh all`.
- **Politeness:** requests are rate-limited and the scripts re-fetch only the current season. Nominatim allows one request per second.
- Source data is public roster pages. Names and hometowns belong to the schools; this project only maps them.
