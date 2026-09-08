#!/usr/bin/env python3
"""
render_fog.py — render Fog of World data to a 1200x1600 RGB image.

Composites (draw order):
  1. Baked street base map (assets/map_base.png)
  2. Dark fog overlay over UNEXPLORED areas
  3. Red tint over areas first explored in the last 30 days (discovery log)

Geometry (BBOX + world projection) is identical to snapshot.py so the
discovery-log array stays pixel-aligned with the world array.

Usage:
    python render_fog.py <fow_data_dir> [out.png]
        fow_data_dir : folder containing a "Sync" subfolder
        out.png      : output path (default: build/fog_rgb.png)
"""
import os
import sys
import math
import logging
import contextlib
from datetime import date, timedelta
from pathlib import Path

import json

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
with contextlib.redirect_stdout(open(os.devnull, "w")):
    import parser as fow

# --- Configuration ---------------------------------------------------------
# Map bounds and the world projection live in geo.py, shared with worldlog so
# the render and the archive can never drift out of alignment.
from config import CFG
from geo import (BBOX_N, BBOX_S, BBOX_W, BBOX_E, CANVAS_W, CANVAS_H,
                 WORLD_RES, bbox_world as _bbox_world)
import geo
import worldlog as wl

BASE_MAP_PATH      = HERE / "assets" / "map_base.png"
WORLD_LOG_DIR      = HERE / "world"
LABELS_PATH        = HERE / "assets" / "labels.json"

LABELS_ON    = CFG["labels"]["enabled"]
LABEL_MAX    = CFG["labels"]["max"]
LABEL_PAD    = CFG["labels"]["pad"]
LABEL_SIZES  = {0: 40, 1: 32, 2: 30, 3: 27, 4: 23, 5: 21}   # by place rank
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]

COLOR_FOG        = tuple(CFG["colors"]["fog"])
FOG_OPACITY      = CFG["colors"]["fog_opacity"]
COLOR_RECENT     = tuple(CFG["colors"]["recent"])
COLOR_WATER      = tuple(CFG["colors"]["water"])
COLOR_WATER_DONE = tuple(CFG["colors"]["water_explored"])
# Re-driving an already-explored street paints a few new px at the edge of the
# old GPS swath (jitter), which read as false "recently explored" marks. Real
# new exploration is a ~10 px swath whose core sits away from old territory:
# require distance from pre-window area and a minimum connected-cluster size.
FRINGE_PX      = CFG["render"]["fringe_px"]
MIN_CLUSTER_PX = CFG["render"]["min_cluster_px"]
# Water is detected in the base map by hue and always shown as water (no fog),
# so a lake reads as water regardless of "exploration". Defaults suit OSM
# Carto; make_base_map.py reports the hues it saw for other tile styles.
WATER_HUE_LO  = CFG["water"]["hue_lo"]
WATER_HUE_HI  = CFG["water"]["hue_hi"]
WATER_SAT_MIN = CFG["water"]["sat_min"]

SSAA, SSAA_THRESH = 4, 20
RECENT_DAYS = CFG["render"]["recent_days"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S", stream=sys.stdout)
log = logging.getLogger("render_fog")

def build_world_array(data_dir):
    """Parse FoW sync data → binary (H×W uint8) world array for the bbox."""
    x0, x1, y0, y1 = _bbox_world()
    W, H = int(math.ceil(x1 - x0)), int(math.ceil(y1 - y0))
    world = np.zeros((H, W), dtype=np.uint8)
    bw = fow.BITMAP_WIDTH

    with contextlib.redirect_stdout(open(os.devnull, "w")):
        fog = fow.FogMap(data_dir)

    for tile in fog.tile_map.values():
        tile_wx = tile.x * fow.TILE_WIDTH * bw
        tile_wy = tile.y * fow.TILE_WIDTH * bw
        for (bx, by), block in tile.blocks.items():
            dx = int(tile_wx + bx * bw - x0)
            dy = int(tile_wy + by * bw - y0)
            if dx + bw <= 0 or dy + bw <= 0 or dx >= W or dy >= H:
                continue
            raw = np.frombuffer(block.bitmap, dtype=np.uint8).reshape(bw, bw // 8)
            bits = np.unpackbits(raw, axis=1)
            sy0 = max(0, -dy); sy1 = min(bw, H - max(0, dy) + sy0)
            sx0 = max(0, -dx); sx1 = min(bw, W - max(0, dx) + sx0)
            if sy1 > sy0 and sx1 > sx0:
                h, w = sy1 - sy0, sx1 - sx0
                world[max(0, dy):max(0, dy) + h, max(0, dx):max(0, dx) + w] |= bits[sy0:sy1, sx0:sx1]

    log.info(f"World array: {W}×{H} — {int(world.sum()):,} explored px")
    return world


def _ssaa(arr):
    """Anti-aliased upscale of a binary mask to canvas size, float 0..1."""
    pil = Image.fromarray(arr * 255, mode="L")
    pil_4x = pil.resize((CANVAS_W * SSAA, CANVAS_H * SSAA), Image.LANCZOS)
    pil_bin = Image.fromarray((np.array(pil_4x) > SSAA_THRESH).astype(np.uint8) * 255, mode="L")
    pil_canvas = pil_bin.resize((CANVAS_W, CANVAS_H), Image.LANCZOS)
    return np.array(pil_canvas).astype(np.float32) / 255.0


def _load_recent_mask(world_shape):
    """Return SSAA'd float mask (0..1) of pixels first explored in last 30d, or None."""
    if WORLD_LOG_DIR.exists() and any(WORLD_LOG_DIR.glob("*.npz")):
        disc = wl.derive_discovery_log(WORLD_LOG_DIR, shape=world_shape)
        log.info(f"Discovery log derived from world archive ({wl.stats(WORLD_LOG_DIR)['days']} days)")
    else:
        log.info("No discovery log — skipping red layer")
        return None
    if disc.shape != world_shape:
        log.warning(f"Discovery log shape {disc.shape} ≠ world {world_shape} — skipping red layer")
        return None
    cutoff = (date.today() - timedelta(days=RECENT_DAYS)).toordinal()
    recent = (disc > 0) & (disc >= cutoff)
    old = (disc > 0) & (disc < cutoff)
    recent &= ~ndimage.binary_dilation(old, iterations=FRINGE_PX)
    labels, _ = ndimage.label(recent)
    sizes = np.bincount(labels.ravel())
    recent = (labels > 0) & (sizes[labels] >= MIN_CLUSTER_PX)
    log.info(f"Recent (30d, fringe-filtered): {int(recent.sum()):,} px")
    return _ssaa(recent.astype(np.uint8))


def _font(size):
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                pass
    try:
        return ImageFont.load_default(size=size)      # Pillow >= 10.1, scalable
    except TypeError:
        return ImageFont.load_default()


def _draw_labels(img):
    """Draw place names over the finished image. No-op if none are baked."""
    if not LABELS_ON:
        return 0
    if not LABELS_PATH.exists():
        log.info("No labels.json — skipping place names (run make_labels.py)")
        return 0
    places = json.loads(LABELS_PATH.read_text())
    x0, x1, y0, y1 = geo.bbox_world()
    draw = ImageDraw.Draw(img)
    placed, n = [], 0

    for pl in sorted(places, key=lambda d: (d["rank"], d["name"])):
        if n >= LABEL_MAX:
            break
        px = (geo.lng_to_wx(pl["lon"]) - x0) / (x1 - x0) * CANVAS_W
        py = (geo.lat_to_wy(pl["lat"]) - y0) / (y1 - y0) * CANVAS_H
        font = _font(LABEL_SIZES.get(pl["rank"], 21))
        l, t, r, b = draw.textbbox((0, 0), pl["name"], font=font)
        w, h = r - l, b - t
        bx0, by0 = px - w / 2, py - h / 2
        box = (bx0 - LABEL_PAD, by0 - LABEL_PAD, bx0 + w + LABEL_PAD, by0 + h + LABEL_PAD)
        if box[0] < 0 or box[1] < 0 or box[2] > CANVAS_W or box[3] > CANVAS_H:
            continue
        if any(box[0] < q[2] and q[0] < box[2] and box[1] < q[3] and q[1] < box[3]
               for q in placed):
            continue
        # white halo then black text: quantises to clean BLACK-on-WHITE
        draw.text((bx0 - l, by0 - t), pl["name"], font=font, fill=(0, 0, 0),
                  stroke_width=3, stroke_fill=(255, 255, 255))
        placed.append(box)
        n += 1
    log.info(f"Labels: drew {n} of {len(places)} places")
    return n


def render(data_dir, out_path):
    world = build_world_array(data_dir)
    explored = _ssaa(world)                       # 0..1 explored coverage
    recent = _load_recent_mask(world.shape)       # 0..1 or None

    if not BASE_MAP_PATH.exists():
        raise FileNotFoundError(f"Base map missing: {BASE_MAP_PATH}")
    base_img = Image.open(BASE_MAP_PATH).convert("RGB")
    base = np.array(base_img, dtype=np.float32)

    # Detect water in the base map by its teal-cyan hue (the lake + river).
    bhsv = np.array(base_img.convert("HSV"))
    water_mask = (
        (bhsv[:, :, 0] >= WATER_HUE_LO) & (bhsv[:, :, 0] <= WATER_HUE_HI)
        & (bhsv[:, :, 1] >= WATER_SAT_MIN)
    )
    log.info(f"Water: {int(water_mask.sum()):,} px")

    # Fog: dark overlay where NOT explored
    fog_alpha = (FOG_OPACITY * (1.0 - explored))[:, :, np.newaxis]
    out = base * (1.0 - fog_alpha) + np.array(COLOR_FOG, dtype=np.float32) * fog_alpha

    # Water (over the fog): blue where untraveled, white where you've been,
    # blended by the anti-aliased explored mask for a soft boundary.
    e = explored[:, :, np.newaxis]
    water_color = (np.array(COLOR_WATER, dtype=np.float32) * (1.0 - e)
                   + np.array(COLOR_WATER_DONE, dtype=np.float32) * e)
    out = np.where(water_mask[:, :, np.newaxis], water_color, out)

    # Red recent tint — after water, so newly traveled water shows red too
    if recent is not None:
        a = recent[:, :, np.newaxis]
        out = out * (1.0 - a) + np.array(COLOR_RECENT, dtype=np.float32) * a

    out = np.clip(out, 0, 255).astype(np.uint8)
    img = Image.fromarray(out, mode="RGB")
    _draw_labels(img)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    log.info(f"Saved → {out_path}")
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    data_dir = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else str(HERE / "build" / "fog_rgb.png")
    render(data_dir, out)
