#!/usr/bin/env python3
"""
make_base_map.py — bake assets/map_base.png for the bbox in config.toml.

The fog is composited pixel-for-pixel onto this image, so it has to be aligned
to the same Web Mercator projection the exploration data uses. That alignment
holds by construction here: Fog of World's global pixel grid is 4,194,304 px
across, which is exactly 256 x 2^14 — the standard XYZ tile grid at zoom 14.
So world pixels are zoom-14 tile pixels, and a tile at zoom z scales by
2^(z-14). This script imports geo.py's projection rather than reimplementing
it, so the base map cannot drift from the renderer.

It fetches raster tiles, stitches them, crops to the aspect-expanded bbox and
downsamples to the canvas. Tiles are cached in .tilecache/ so re-runs are free.

Usage:
    python make_base_map.py [--zoom N] [--out assets/map_base.png] [--force]
"""
import sys
import time
import math
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

import geo
from config import CFG

HERE = Path(__file__).parent
CACHE = HERE / ".tilecache"
TILE = 256
BASE_ZOOM = 14          # zoom whose pixel grid equals Fog of World's world grid
MAX_ZOOM = 19
TILE_BUDGET = 1200      # refuse runaway downloads without --force


def pick_zoom(span_px, target_px):
    """Smallest zoom whose width covers the canvas, plus one for downsampling."""
    z = BASE_ZOOM
    while span_px * 2 ** (z - BASE_ZOOM) < target_px and z < MAX_ZOOM:
        z += 1
    return min(z + 1, MAX_ZOOM)


def fetch(url, ua, tries=3):
    CACHE.mkdir(exist_ok=True)
    name = url.split("://", 1)[-1].replace("/", "_")
    cached = CACHE / name
    if cached.exists():
        return cached.read_bytes()
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            cached.write_bytes(data)
            time.sleep(0.05)
            return data
        except urllib.error.HTTPError as e:
            if e.code in (404, 403) or attempt == tries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))


def build(zoom=None, out=None, force=False):
    bm = CFG["basemap"]
    ua = bm.get("user_agent") or "fogframe/1.0"
    if "YOURNAME" in ua:
        print("! set [basemap] user_agent in config.toml to a real contact "
              "before fetching from a public tile server", file=sys.stderr)

    x0, x1, y0, y1 = geo.bbox_world()
    z = zoom or (bm.get("zoom") or 0) or pick_zoom(x1 - x0, geo.CANVAS_W)
    s = 2.0 ** (z - BASE_ZOOM)
    px0, px1, py0, py1 = x0 * s, x1 * s, y0 * s, y1 * s

    tx0, tx1 = int(math.floor(px0 / TILE)), int(math.ceil(px1 / TILE))
    ty0, ty1 = int(math.floor(py0 / TILE)), int(math.ceil(py1 / TILE))
    nx, ny = tx1 - tx0, ty1 - ty0
    n = nx * ny
    print(f"zoom {z}: {nx} x {ny} = {n} tiles "
          f"({(px1-px0):.0f} x {(py1-py0):.0f} px -> {geo.CANVAS_W} x {geo.CANVAS_H})")
    if n > TILE_BUDGET and not force:
        sys.exit(f"{n} tiles exceeds the {TILE_BUDGET}-tile budget. "
                 f"Lower [basemap] zoom, shrink the bbox, or pass --force.")

    canvas = Image.new("RGB", (nx * TILE, ny * TILE), (255, 255, 255))
    for i, tx in enumerate(range(tx0, tx1)):
        for j, ty in enumerate(range(ty0, ty1)):
            url = bm["tile_url"].format(z=z, x=tx, y=ty)
            try:
                import io
                canvas.paste(Image.open(io.BytesIO(fetch(url, ua))).convert("RGB"),
                             (i * TILE, j * TILE))
            except Exception as e:
                print(f"  tile {z}/{tx}/{ty} failed ({e}) — left blank", file=sys.stderr)
        print(f"\r  column {i+1}/{nx}", end="", file=sys.stderr)
    print(file=sys.stderr)

    crop = (int(round(px0 - tx0 * TILE)), int(round(py0 - ty0 * TILE)),
            int(round(px1 - tx0 * TILE)), int(round(py1 - ty0 * TILE)))
    img = canvas.crop(crop).resize((geo.CANVAS_W, geo.CANVAS_H), Image.LANCZOS)

    out = Path(out or HERE / "assets" / "map_base.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(f"saved {out} ({img.size[0]}x{img.size[1]})")
    report_hues(img)
    print(f"attribution: {bm.get('attribution', '')}")
    return out


def report_hues(img):
    """Print the dominant saturated hues, to help retune [water] for a style."""
    hsv = np.array(img.convert("HSV"))
    h, s = hsv[:, :, 0], hsv[:, :, 1]
    sat = s >= 40
    if not sat.any():
        print("no saturated pixels — a greyscale style needs [water] disabled")
        return
    counts = np.bincount(h[sat], minlength=256)
    top = np.argsort(counts)[::-1][:6]
    total = counts.sum()
    print("\ndominant saturated hues (for [water] hue_lo/hue_hi):")
    for hue in top:
        if counts[hue]:
            print(f"  hue {hue:3d}  {100*counts[hue]/total:5.1f}%")
    lo, hi = CFG["water"]["hue_lo"], CFG["water"]["hue_hi"]
    got = int(((h >= lo) & (h <= hi) & sat).sum())
    print(f"current [water] {lo}-{hi} selects {got:,} px "
          f"({100*got/h.size:.1f}% of the image)")


if __name__ == "__main__":
    a = sys.argv[1:]
    z = int(a[a.index("--zoom") + 1]) if "--zoom" in a else None
    o = a[a.index("--out") + 1] if "--out" in a else None
    sys.exit(0 if build(z, o, "--force" in a) else 0)
