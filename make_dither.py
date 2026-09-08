#!/usr/bin/env python3
"""
make_dither.py — generate the blue-noise threshold mask used for neutral tones.

The panel has no grey: flat areas are made by dithering black and white. An
ordered Bayer matrix does that with a fixed 8x8 grid, which the eye reads as
regularly spaced dots rather than tone — distracting next to the thin red
exploration lines that are the point of the image.

Blue noise has the same average density but no periodic structure, so it reads
as smooth tone. Built by void-and-cluster (Ulichney 1993): repeatedly relocate
the tightest cluster into the largest void, then rank every pixel by the order
it would be added or removed.

Deterministic for a given seed. Run once; the result is committed.

Usage:  python make_dither.py [--size 64] [--out assets/bluenoise.npy]
"""
import sys
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter

HERE = Path(__file__).parent
SIGMA = 1.9


def _filt(b):
    return gaussian_filter(b.astype(np.float64), sigma=SIGMA, mode="wrap")


def _tightest_cluster(b, f):
    return np.unravel_index(np.argmax(np.where(b, f, -np.inf)), b.shape)


def _largest_void(b, f):
    return np.unravel_index(np.argmin(np.where(~b, f, np.inf)), b.shape)


def generate(n=64, seed=7):
    rng = np.random.default_rng(seed)
    total = n * n
    ones = total // 10
    b = np.zeros((n, n), bool)
    b.flat[rng.choice(total, ones, replace=False)] = True

    # phase 0 — spread the seed pattern until it stops moving
    for _ in range(10 * total):
        c = _tightest_cluster(b, _filt(b))
        b[c] = False
        v = _largest_void(b, _filt(b))
        b[v] = True
        if v == c:
            break
    proto = b.copy()

    rank = np.zeros((n, n), np.int32)
    # phase 1 — remove points, ranking downward
    b = proto.copy()
    for r in range(ones - 1, -1, -1):
        c = _tightest_cluster(b, _filt(b))
        b[c] = False
        rank[c] = r
    # phase 2 — add points, ranking upward
    b = proto.copy()
    for r in range(ones, total):
        v = _largest_void(b, _filt(b))
        b[v] = True
        rank[v] = r
    return rank


if __name__ == "__main__":
    a = sys.argv[1:]
    n = int(a[a.index("--size") + 1]) if "--size" in a else 64
    out = Path(a[a.index("--out") + 1] if "--out" in a else HERE / "assets" / "bluenoise.npy")
    print(f"generating {n}x{n} blue noise (void-and-cluster)...")
    rank = generate(n)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.save(out, rank.astype(np.uint16))
    print(f"saved {out}")
