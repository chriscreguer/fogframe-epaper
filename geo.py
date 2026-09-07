#!/usr/bin/env python3
"""
geo.py — bbox geometry and the Fog of World world-pixel projection.

Single source of truth for the map bounds and the Web Mercator projection.
render_fog, snapshot and worldlog all import from here so the rendered image,
the snapshot arrays and the world delta log can never drift out of alignment.
"""
import os
import sys
import math
import contextlib
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
with contextlib.redirect_stdout(open(os.devnull, "w")):
    import parser as fow

# --- Map bounds (from config.toml) ------------------------------------------
from config import CFG

BBOX_N = CFG["map"]["north"]
BBOX_S = CFG["map"]["south"]
BBOX_W = CFG["map"]["west"]
BBOX_E = CFG["map"]["east"]
CANVAS_W = CFG["canvas"]["width"]
CANVAS_H = CFG["canvas"]["height"]

# Fog of World's global pixel grid: 512 tiles x 128 blocks x 64 px = 4,194,304
WORLD_RES  = fow.MAP_WIDTH * fow.TILE_WIDTH * fow.BITMAP_WIDTH
BLOCK_W    = fow.BITMAP_WIDTH            # 64 px per block side
TILE_PX    = fow.TILE_WIDTH * BLOCK_W    # 8192 px per tile side
BLOCK_BYTES = BLOCK_W * BLOCK_W // 8     # 512 bytes per block bitmap


def lng_to_wx(lng):
    return (lng + 180.0) / 360.0 * WORLD_RES


def lat_to_wy(lat):
    return (math.pi - math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0))) \
           / (2.0 * math.pi) * WORLD_RES


def expand_to_aspect(x0, x1, y0, y1, tw, th):
    """Grow the shorter axis so the box matches the canvas aspect ratio."""
    w, h = x1 - x0, y1 - y0
    if w / h < tw / th:
        cx = (x0 + x1) / 2.0
        hw = h * tw / th / 2.0
        x0, x1 = cx - hw, cx + hw
    else:
        cy = (y0 + y1) / 2.0
        hh = w * th / tw / 2.0
        y0, y1 = cy - hh, cy + hh
    return x0, x1, y0, y1


def bbox_world():
    """The bbox in world-pixel coords, expanded to the canvas aspect."""
    x0, x1 = lng_to_wx(BBOX_W), lng_to_wx(BBOX_E)
    y0, y1 = lat_to_wy(BBOX_N), lat_to_wy(BBOX_S)
    return expand_to_aspect(x0, x1, y0, y1, CANVAS_W, CANVAS_H)


def bbox_shape():
    """(H, W) of the world array covering the bbox."""
    x0, x1, y0, y1 = bbox_world()
    return int(math.ceil(y1 - y0)), int(math.ceil(x1 - x0))


def block_offset(key):
    """
    (dy, dx) placing a block's top-left corner in the bbox array.

    Mirrors render_fog.build_world_array exactly, including its quirk: the
    offset is int(block_coord - origin) on a float origin, and int() truncates
    toward zero. For blocks straddling the top or left edge that difference is
    negative, so the result is NOT floor and NOT a constant translation. Every
    render and every recorded day used this mapping, so the archive reproduces
    it rather than correcting it.
    """
    x0, _, y0, _ = bbox_world()
    gx, gy = block_origin(key)
    return int(gy - y0), int(gx - x0)


def clip_block(dy, dx, H, W, bw=None):
    """Block-local and bbox-local slices for a block at (dy, dx). None if outside."""
    bw = bw or BLOCK_W
    if dx + bw <= 0 or dy + bw <= 0 or dx >= W or dy >= H:
        return None
    sy0 = max(0, -dy); sy1 = min(bw, H - max(0, dy) + sy0)
    sx0 = max(0, -dx); sx1 = min(bw, W - max(0, dx) + sx0)
    if sy1 <= sy0 or sx1 <= sx0:
        return None
    return (sy0, sy1, sx0, sx1), (max(0, dy), max(0, dx))


def block_key(gx, gy):
    """Global pixel coord -> (tile_x, tile_y, block_x, block_y)."""
    return (gx // TILE_PX, gy // TILE_PX,
            (gx % TILE_PX) // BLOCK_W, (gy % TILE_PX) // BLOCK_W)


def block_origin(key):
    """(tile_x, tile_y, block_x, block_y) -> global pixel coord of its corner."""
    tx, ty, bx, by = key
    return tx * TILE_PX + bx * BLOCK_W, ty * TILE_PX + by * BLOCK_W
