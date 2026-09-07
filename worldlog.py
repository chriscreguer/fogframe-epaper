#!/usr/bin/env python3
"""
worldlog.py — append-only, whole-world delta archive of Fog of World data.

Instead of storing a dense per-day array cropped to the map bbox, this records
only the blocks that changed each day, and within them only the bits that
turned on. That covers the entire planet at a fraction of the size, and lets
the bbox-specific discovery log be *derived* rather than stored — so changing
the map bounds no longer destroys exploration history.

Layout:
    world/meta.json          database id + format version
    world/YYYY-MM-DD.npz     one delta per day that saw new exploration

Each delta holds:
    keys         (N, 4) int32   tile_x, tile_y, block_x, block_y
    added        (N, 512) uint8 packed 64x64 bitmap of bits that turned ON
    removed_keys (M, 4) int32   blocks that lost bits
    removed      (M, 512) uint8 packed bitmap of bits that turned OFF

Replay is ADDITIVE: it ORs `added` and ignores `removed`, so the archive
accumulates the union of everything Fog of World has ever reported and acts as
a durable record rather than a mirror of upstream state. Removals are still
written so a quiet day can be told apart from a sync that dropped data.
"""
import os
import sys
import json
import logging
import contextlib
from datetime import date
from pathlib import Path

import numpy as np

import geo

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
with contextlib.redirect_stdout(open(os.devnull, "w")):
    import parser as fow

FORMAT_VERSION = 1
log = logging.getLogger("worldlog")

BB = geo.BLOCK_BYTES


# --- reading Fog of World ---------------------------------------------------

def scan(data_dir):
    """Parse a FoW database -> {block_key: packed (512,) uint8 bitmap}."""
    with contextlib.redirect_stdout(open(os.devnull, "w")):
        fog = fow.FogMap(str(data_dir))
    state = {}
    for tile in fog.tile_map.values():
        for (bx, by), block in tile.blocks.items():
            key = (int(tile.x), int(tile.y), int(bx), int(by))
            state[key] = np.frombuffer(block.bitmap, dtype=np.uint8).copy()
    return state


# --- writing deltas ---------------------------------------------------------

def _stack(pairs):
    if not pairs:
        return (np.zeros((0, 4), np.int32), np.zeros((0, BB), np.uint8))
    keys = np.array([k for k, _ in pairs], dtype=np.int32)
    bits = np.stack([b for _, b in pairs]).astype(np.uint8)
    return keys, bits


def diff(prev, cur):
    """(added, removed) as lists of (key, packed bitmap), bits that changed."""
    added, removed = [], []
    zero = np.zeros(BB, np.uint8)
    for key, bits in cur.items():
        p = prev.get(key, zero)
        a = bits & ~p
        if a.any():
            added.append((key, a))
    for key, bits in prev.items():
        c = cur.get(key, zero)
        r = bits & ~c
        if r.any():
            removed.append((key, r))
    return added, removed


def write_delta(prev, cur, day, log_dir):
    """Write world/<day>.npz for the change from prev to cur. None if no change."""
    added, removed = diff(prev, cur)
    if not added and not removed:
        return None
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    ak, ab = _stack(added)
    rk, rb = _stack(removed)
    path = log_dir / f"{day}.npz"
    np.savez_compressed(path, keys=ak, added=ab, removed_keys=rk, removed=rb)
    return path


def write_meta(log_dir, database_id):
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "meta.json").write_text(json.dumps(
        {"format_version": FORMAT_VERSION, "database_id": database_id}, indent=2) + "\n")


# --- reading the log --------------------------------------------------------

def delta_paths(log_dir):
    """Every delta file, ascending by date."""
    return sorted(Path(log_dir).glob("*.npz"))


def load_delta(path):
    d = np.load(path)
    return d["keys"], d["added"], d["removed_keys"], d["removed"]


def replay(log_dir, upto=None, before=None):
    """
    Cumulative world state. Additive: ignores removals.

    `upto`   include deltas up to and including this date.
    `before` include deltas strictly before this date. Callers rewriting a
             day's own delta MUST pass it, or that day's already-recorded
             pixels land in `prev` and the rewrite drops them.
    """
    state = {}
    for p in delta_paths(log_dir):
        if upto is not None and p.stem > str(upto):
            break
        if before is not None and p.stem >= str(before):
            break
        keys, added, _, _ = load_delta(p)
        for key, bits in zip(map(tuple, keys.tolist()), added):
            if key in state:
                state[key] |= bits
            else:
                state[key] = bits.copy()
    return state


def derive_discovery_log(log_dir, shape=None):
    """
    Rebuild the bbox discovery log from the archive: per-pixel first-seen date
    ordinal, int32, zero where never explored. Same semantics as the file
    snapshot.py used to write, but derived from whole-world history.
    """
    H, W = shape or geo.bbox_shape()
    disc = np.zeros((H, W), dtype=np.int32)
    bw = geo.BLOCK_W

    for p in delta_paths(log_dir):
        try:
            ordinal = date.fromisoformat(p.stem).toordinal()
        except ValueError:
            continue
        keys, added, _, _ = load_delta(p)
        for key, packed in zip(map(tuple, keys.tolist()), added):
            dy, dx = geo.block_offset(key)
            clip = geo.clip_block(dy, dx, H, W)
            if clip is None:
                continue
            (sy0, sy1, sx0, sx1), (ty, tx) = clip
            bits = np.unpackbits(packed.reshape(bw, bw // 8), axis=1)
            sub = disc[ty:ty + (sy1 - sy0), tx:tx + (sx1 - sx0)]
            new = (bits[sy0:sy1, sx0:sx1] == 1) & (sub == 0)
            sub[new] = ordinal
    return disc


def total_px(log_dir):
    """Explored pixels the archive replays to, worldwide."""
    return sum(int(np.unpackbits(b).sum()) for b in replay(log_dir).values())


def check_monotonic(log_dir):
    """
    Assert the archive never shrinks, and record the new high-water mark.

    Replay is additive, so the pixel count can only ever grow. A drop means
    a delta was overwritten or truncated — the failure mode where a second
    run in one day rewrote that day's file with only its increment. Cheap to
    run (a few hundred KB of deltas) and it fails the build loudly instead of
    silently losing history.
    """
    log_dir = Path(log_dir)
    meta_path = log_dir / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    now = total_px(log_dir)
    was = meta.get("total_px")
    if was is not None and now < was:
        raise SystemExit(
            f"ARCHIVE REGRESSION: replays to {now:,} px, was {was:,} "
            f"({was - now:,} lost). Refusing to continue.")
    meta.update({"format_version": FORMAT_VERSION, "total_px": now,
                 "days": len(delta_paths(log_dir))})
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")
    return now, was


def stats(log_dir):
    paths = delta_paths(log_dir)
    total = sum(p.stat().st_size for p in paths)
    return {"days": len(paths), "bytes": total,
            "first": paths[0].stem if paths else None,
            "last": paths[-1].stem if paths else None}
