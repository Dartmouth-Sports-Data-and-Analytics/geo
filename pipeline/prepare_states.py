"""prepare_states.py — fetches US state outlines once, fixes their polygon winding for d3, and writes docs/us-states.json."""
import argparse
import json
import os
import urllib.request

import config

SOURCE_URL = "https://raw.githubusercontent.com/PublicaMundi/MappingAPI/master/data/geojson/us-states.json"
OUT_PATH = os.path.join(config.DOCS_DIR, "us-states.json")
DROP = {"Puerto Rico"}


# Planar signed area of a lon/lat ring (> 0 means counter-clockwise); longitudes are unwrapped first so rings across the antimeridian work.
def signed_area(ring):
    pts = [list(ring[0][:2])]
    for x, y in (p[:2] for p in ring[1:]):
        while x - pts[-1][0] > 180:
            x -= 360
        while x - pts[-1][0] < -180:
            x += 360
        pts.append([x, y])
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:])) / 2


# d3 reads outer rings clockwise and holes counter-clockwise; anything else is taken to mean "the whole globe except this shape".
def rewind_polygon(rings):
    out = []
    for i, ring in enumerate(rings):
        area = signed_area(ring)
        wrong = area > 0 if i == 0 else area < 0
        out.append(ring[::-1] if wrong else ring)
    return out


def rewind_geometry(geometry):
    if geometry["type"] == "Polygon":
        return {"type": "Polygon", "coordinates": rewind_polygon(geometry["coordinates"])}
    if geometry["type"] == "MultiPolygon":
        return {"type": "MultiPolygon", "coordinates": [rewind_polygon(p) for p in geometry["coordinates"]]}
    return geometry


def load_source(source):
    if source.startswith(("http://", "https://")):
        request = urllib.request.Request(source, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    with open(source, encoding="utf-8") as f:
        return json.load(f)


def prepare(source=SOURCE_URL, out_path=OUT_PATH):
    raw = load_source(source)
    features = [
        {"type": "Feature", "properties": {"name": f["properties"]["name"]}, "geometry": rewind_geometry(f["geometry"])}
        for f in raw["features"] if f["properties"]["name"] not in DROP
    ]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, separators=(",", ":"))
    print(f"Wrote {out_path}: {len(features)} states, {os.path.getsize(out_path) / 1e3:.0f} KB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prepare docs/us-states.json for the heat maps.")
    parser.add_argument("source", nargs="?", default=SOURCE_URL, help="URL or local file of the raw state outlines (default: the PublicaMundi file).")
    prepare(parser.parse_args().source)
