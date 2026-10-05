# Ivy League Athletics Roster Map

A static, data-driven map of where Ivy League athletes come from (2021–2025 rosters, all sports, all eight schools). Built as plain HTML/CSS/JS + a single JSON data file — no R, no server, no build step.

## Files

- `index.html` — page shell
- `style.css` — dark map theme + filter panel
- `app.js` — loads `data.json`, renders ~28.6k points with Leaflet, and wires up the school/year/sport filters
- `data.json` — the roster data (lat, lng, name, school, sport, year, hometown), in a compact columnar format

## Run it locally

Any static file server works, e.g.:

```bash
python3 -m http.server 8000
```

Then open `http://localhost:8000`.

## Map tiles

The basemap comes from Stadia Maps (`alidade_smooth_dark`). It works with
**no signup at all on `localhost`**, so local preview just works. Once the
site is live on a real domain (e.g. `github.io`), Stadia requires either:

- **Domain-based auth (recommended, no code change):** sign up free at
  [client.stadiamaps.com](https://client.stadiamaps.com/dashboard/), add your
  `*.github.io` domain under "Manage Properties" → "Authentication
  Configuration", and it'll just work — the browser's own `Origin` header is
  what gets checked, nothing to change in `app.js`.
- **API key:** append `?api_key=YOUR-KEY` to the tile URL in `app.js` instead.

Free tier is generous (well beyond what a roster map will use), no credit
card required either way.

## Deploy to GitHub Pages

1. Create a new GitHub repository (or use an existing one) and push these four files to it:

   ```bash
   git init
   git add index.html style.css app.js data.json README.md
   git commit -m "Ivy roster map"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<your-repo>.git
   git push -u origin main
   ```

2. On GitHub, go to your repo's **Settings → Pages**.
3. Under **Build and deployment**, set **Source** to **Deploy from a branch**, pick the `main` branch and the `/ (root)` folder, then **Save**.
4. GitHub will publish the site at `https://<your-username>.github.io/<your-repo>/` within a minute or two. Refresh that Pages settings page if the URL doesn't appear right away.

Because everything here is static (no server-side code), Pages is a natural fit — pushing an updated `data.json` after each season is all you'll need to do to refresh the map.

## Updating the data

Re-run your R scraping + geocoding pipeline, export the result as the same four columns (`lat`, `lng`, `name`, `school`, `sport`, `year`, `hometown`), and write it out in this columnar JSON shape:

```json
{
  "lat": [..], "lng": [..], "name": [..],
  "school": [..], "sport": [..], "year": [..], "hometown": [..]
}
```

Replace `data.json` with the new file and push — no other changes needed.
