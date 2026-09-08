#!/usr/bin/env python3
"""
make_labels.py — bake place names for the bbox into assets/labels.json.

Queried once from Overpass (no account, no key) and cached, so rendering stays
offline and the daily build does not depend on a third-party API being up.

Labels are drawn by render_fog AFTER the fog overlay, not baked into the base
map, so they stay legible over unexplored areas instead of being dimmed and
dithered along with everything underneath.

Usage:  python make_labels.py [--out assets/labels.json]
"""
import sys
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path

import geo

HERE = Path(__file__).parent
OVERPASS = "https://overpass-api.de/api/interpreter"

# Bigger places get bigger type. Anything not listed is skipped.
RANK = {"city": 0, "borough": 1, "town": 2, "suburb": 3, "quarter": 4,
        "neighbourhood": 5, "village": 5}


def _bbox_latlon():
    """The aspect-expanded bbox actually rendered, as (S, W, N, E)."""
    x0, x1, y0, y1 = geo.bbox_world()
    def lon(wx): return wx / geo.WORLD_RES * 360.0 - 180.0
    def lat(wy):
        n = math.pi - 2.0 * math.pi * wy / geo.WORLD_RES
        return math.degrees(math.atan(math.sinh(n)))
    return lat(y1), lon(x0), lat(y0), lon(x1)


def fetch():
    s, w, n, e = _bbox_latlon()
    kinds = "|".join(RANK)
    q = (f'[out:json][timeout:40];node["place"~"^({kinds})$"]'
         f'({s:.6f},{w:.6f},{n:.6f},{e:.6f});out body;')
    req = urllib.request.Request(OVERPASS, data=urllib.parse.urlencode({"data": q}).encode(),
                                 headers={"User-Agent": "fogframe/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        elements = json.load(r).get("elements", [])

    out = []
    for el in elements:
        t = el.get("tags", {})
        name, place = t.get("name"), t.get("place")
        if not name or place not in RANK:
            continue
        out.append({"name": name, "lat": el["lat"], "lon": el["lon"],
                    "place": place, "rank": RANK[place]})
    out.sort(key=lambda d: (d["rank"], d["name"]))
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    out_path = Path(a[a.index("--out") + 1] if "--out" in a else HERE / "assets" / "labels.json")
    s, w, n, e = _bbox_latlon()
    print(f"querying Overpass for {s:.4f},{w:.4f} .. {n:.4f},{e:.4f}")
    labels = fetch()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(labels, indent=1, ensure_ascii=False) + "\n")
    print(f"saved {len(labels)} places -> {out_path}")
    for d in labels[:12]:
        print(f"  {d['place']:<14}{d['name']}")
