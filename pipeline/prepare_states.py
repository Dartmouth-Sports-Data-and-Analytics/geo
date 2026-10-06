"""prepare_states.py — fetches US state outlines once, fixes their polygon winding for d3, and writes docs/us-states.json."""
import argparse
import json
import math
import os
import urllib.request

import config

SOURCE_URL = "https://raw.githubusercontent.com/PublicaMundi/MappingAPI/master/data/geojson/us-states.json"
OUT_PATH = os.path.join(config.DOCS_DIR, "us-states.json")
DROP = {"Puerto Rico"}


TAU = 2 * math.pi


# Spherical area (steradians) of a polygon made of the given rings, exactly as d3.geoArea measures it: a clockwise outer ring
# gives its true (small) area, a counter-clockwise one gives the rest of the globe, and a counter-clockwise hole subtracts.
def polygon_area(rings):
    total = 0.0
    for ring in rings:
        pts = ring[:-1] if ring[0] == ring[-1] else ring
        lam0 = math.radians(pts[0][0])
        phi = math.radians(pts[0][1]) / 2 + math.pi / 4
        cos0, sin0 = math.cos(phi), math.sin(phi)
        for lon, lat in pts[1:] + [pts[0]]:
            lam, phi = math.radians(lon), math.radians(lat) / 2 + math.pi / 4
            d_lam = lam - lam0
            sign = 1 if d_lam >= 0 else -1
            cos_p, sin_p = math.cos(phi), math.sin(phi)
            k = sin0 * sin_p
            total += math.atan2(k * sign * math.sin(sign * d_lam), cos0 * cos_p + k * math.cos(sign * d_lam))
            lam0, cos0, sin0 = lam, cos_p, sin_p
    return 2 * (TAU + total if total < 0 else total)


def true_area(ring):
    area = polygon_area([ring])
    return min(area, 2 * TAU - area)


# Even-odd point-in-ring test on flat lon/lat, with longitudes unwrapped so rings that cross the antimeridian still work.
def ring_contains(ring, point):
    xs = [ring[0][0]]
    for x, _ in ring[1:]:
        while x - xs[-1] > 180:
            x -= 360
        while x - xs[-1] < -180:
            x += 360
        xs.append(x)
    px = point[0]
    while px - xs[0] > 180:
        px -= 360
    while px - xs[0] < -180:
        px += 360
    inside = False
    for (x1, y1), (x2, y2) in zip(zip(xs, (p[1] for p in ring)), zip(xs[1:], (p[1] for p in ring[1:]))):
        if (y1 > point[1]) != (y2 > point[1]) and px < (x2 - x1) * (point[1] - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


# Works out outer rings and holes by position (the file's ring order and roles can't be trusted), then winds them the way d3 needs.
def rebuild_polygons(rings):
    areas = [true_area(r) for r in rings]
    samples = [[r[0], r[len(r) // 2], r[-2]] for r in rings]
    outers, holes = [], {}
    for i, ring in enumerate(rings):
        containers = [j for j in range(len(rings))
                      if j != i and areas[j] > areas[i] and sum(ring_contains(rings[j], p) for p in samples[i]) >= 2]
        if len(containers) % 2:
            holes.setdefault(min(containers, key=lambda j: areas[j]), []).append(i)
        else:
            outers.append(i)
    polygons = []
    for i in outers:
        outer = rings[i] if polygon_area([rings[i]]) < TAU else rings[i][::-1]
        inner = [rings[h] if polygon_area([rings[h]]) > TAU else rings[h][::-1] for h in holes.get(i, [])]
        polygons.append([outer] + inner)
    return polygons


def rewind_geometry(geometry):
    if geometry["type"] == "Polygon":
        rings = geometry["coordinates"]
    elif geometry["type"] == "MultiPolygon":
        rings = [r for polygon in geometry["coordinates"] for r in polygon]
    else:
        return geometry
    polygons = rebuild_polygons(rings)
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def feature_area(geometry):
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    return sum(polygon_area(p) for p in polygons)


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

    # The heat map page refuses any outline whose area d3 reads as more than half the globe; check the same thing here.
    bad = [f["properties"]["name"] for f in features if feature_area(f["geometry"]) > TAU]
    if bad:
        print(f"WARNING: still wrongly wound after preparing: {', '.join(bad)}")
        for f in features:
            if f["properties"]["name"] in bad:
                g = f["geometry"]
                polygons = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
                print(f"  {f['properties']['name']}: {[[len(r) for r in p] for p in polygons]} points per ring")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prepare docs/us-states.json for the heat maps.")
    parser.add_argument("source", nargs="?", default=SOURCE_URL, help="URL or local file of the raw state outlines (default: the PublicaMundi file).")
    prepare(parser.parse_args().source)
