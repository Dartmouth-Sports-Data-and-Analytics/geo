# Ivy League Athletics Roster Map

An interactive map of where Ivy League varsity athletes come from. A Python pipeline scrapes every roster page on the eight schools' athletics sites, geocodes each hometown, and writes one JSON file that a static Leaflet page draws as one dot per athlete.

**Live site:** `https://<your-username>.github.io/<your-repo>/`

## What the map does

- One dot per person, colored by school; clicking it shows their sports, seasons and hometown.
- Filter by school, season, sport and region, and search by name.
- A separate **State heat maps** page shows all eight schools at once, each a US map shaded by players per state; hover a state for its player count and sports.
- A person on two teams (for example coed and women's sailing) is one dot with both listed.
- "Year" always means the academic year: 2024 is the 2024–25 season, for every sport.

## Structure

```
pipeline/   Python scripts (code only)
data/       Inputs, scraped rosters, and pipeline state
docs/       The website, published by GitHub Pages
```

| Path | What it is |
|---|---|
| `pipeline/run_pipeline.py` | Runs the four steps below in order |
| `pipeline/discover_sports.py` | Finds which school + sport pages exist and the working URL for each |
| `pipeline/pull_rosters.py` | Downloads each season's roster, drops stale or copied seasons |
| `pipeline/geocode_rosters.py` | Turns hometowns into coordinates (OpenStreetMap Nominatim), with a cache |
| `pipeline/build_data_json.py` | Merges everything into `docs/data.json`, one entry per athlete per sport |
| `pipeline/config.py` | Paths, school list and seasons, and loaders for the hand-edited input files |
| `pipeline/site_rules.py` | Hand-confirmed quirks of each school's site (URL patterns, missing seasons, unavailable teams); edit these tables, not the code |
| `pipeline/scraper.py` | Fetching and parsing roster pages, with polite rate limiting |
| `pipeline/availability.py` | The cache of which school + sport pages exist, and the rules that tidy it |
| `pipeline/roster_data.py` | Name cleanup and the stale-season checks for roster tables |
| `pipeline/prepare_states.py` | One-time: downloads US state outlines and fixes them for the heat maps |
| `pipeline/stamp_versions.py` | Updates the cache-busting version suffixes in `docs/` from file hashes |
| `pipeline/audit_rosters.py` | Read-only checks for suspicious rosters |
| `data/sport_page_reference.xlsx` | Hand-edited list of sports and URL slugs |
| `data/hometown_fixes.csv` | Hand-edited corrections for misspelled hometowns |
| `data/rosters/`, `data/geo-rosters/` | Scraped rosters, and the same with coordinates added |
| `data/_*.csv` | Pipeline state: availability, hometown cache, failed lookups, scrape log |
| `docs/index.html`, `app.js`, `style.css` | The roster map |
| `docs/shared.js` | Constants and helpers used by both pages (school colors, formatting, chips) |
| `docs/heatmaps.html`, `heatmaps.js`, `heatmaps.css` | The eight state heat maps (uses d3) |
| `docs/data.json`, `docs/us-states.json` | Built roster data; US state outlines (downloaded once, see below) |

## Running it

```bash
python3 -m venv venv && source venv/bin/activate
pip install pandas requests beautifulsoup4 openpyxl geopy

cd pipeline
python run_pipeline.py                         # discover -> pull -> geocode -> build
python run_pipeline.py --only pull             # one step
python run_pipeline.py --refresh baseball      # refetch every season of a sport
python geocode_rosters.py --retry-failed       # retry hometowns with no match

cd ../docs
python3 -m http.server 8000                    # preview at http://localhost:8000
```

The heat maps need US state outlines once: `python prepare_states.py` (from `pipeline/`) downloads them, fixes their polygon winding, and writes `docs/us-states.json`.

Cache-busting `?v=` suffixes in `docs/` are content hashes, stamped automatically at the end of `build_data_json.py`. After editing `app.js`, `heatmaps.js` or the CSS by hand, run `python stamp_versions.py` (or install the pre-commit hook below) so browsers fetch the new files. To stamp automatically on every commit, install this hook once from the repo root:

```bash
printf '#!/bin/sh\npython3 pipeline/stamp_versions.py && git add docs\n' > .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

To publish: commit and push `docs/data.json`. In **Settings → Pages**, deploy from branch `main`, folder `/docs`.

## Notes

- **Map tiles** come from Stadia Maps, which works without signup on `localhost`. On a `github.io` address, add the domain in your Stadia dashboard (or use an API key).
- **Data quality:** hometowns are shown as the schools publish them, and a few cannot be geocoded, so those athletes have no dot. Athletes can appear for five seasons when a school lists a fifth year.
- **Politeness:** requests are rate-limited and the scripts re-fetch only the current season. Nominatim allows one request per second.
- Source data is public roster pages. Names and hometowns belong to the schools; this project only maps them.
