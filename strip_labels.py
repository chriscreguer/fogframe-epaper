#!/usr/bin/env python3
"""
strip_labels.py — remove baked-in place labels from assets/map_base.png.

The base map was rendered from a Mapbox style with labels drawn in, so the
frame showed neighbourhood names that no longer come from anywhere in this
codebase. They cannot be turned off after the fact — but they can be painted
out, because the label text is the only genuinely dark thing in the style:
navy glyphs around luminance 90-140, where roads, parkland and water all sit
above 200. Under 0.2% of the image qualifies.

Masked pixels are filled by inpainting inward from the surrounding map: each
pass sets the masked pixels that touch known ones to the mean of those
neighbours, then repeats. A median filter is not enough — inside a bold glyph
the window is mostly glyph, so it leaves a visible ghost.

Usage:  python strip_labels.py [--in assets/map_base.png] [--thresh 150] [--dilate 3]
"""
import sys
import shutil
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

HERE = Path(__file__).parent


def strip(path, thresh=150, dilate=3):
    img = Image.open(path).convert("RGB")
    a = np.asarray(img, np.uint8)
    lum = 0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]

    mask = lum < thresh
    if dilate:
        mask = ndimage.binary_dilation(mask, iterations=dilate)
    print(f"label mask: {mask.sum():,} px ({100*mask.mean():.3f}% of image)")

    out = a.astype(np.float64)
    todo = mask.copy()
    known = (~mask).astype(np.float64)
    vals = out * known[:, :, None]
    passes = 0
    while todo.any():
        # count of known neighbours, not a fraction: uniform_filter on a mostly
        # zero float array leaves rounding dust, and dividing by that explodes
        # into saturated colour speckle. Require at least one real neighbour.
        k = ndimage.uniform_filter(known, 3) * 9.0
        fill = todo & (k >= 0.5)
        if not fill.any():
            break
        for c in range(3):
            v = ndimage.uniform_filter(vals[:, :, c], 3) * 9.0
            out[:, :, c][fill] = v[fill] / k[fill]
        known[fill] = 1.0
        vals[fill] = out[fill]
        todo &= ~fill
        passes += 1
    print(f"inpainted in {passes} passes")
    out = np.clip(out, 0, 255).astype(np.uint8)

    remaining = int(((0.299*out[:,:,0] + 0.587*out[:,:,1] + 0.114*out[:,:,2]) < thresh).sum())
    print(f"dark pixels remaining: {remaining:,}")
    return Image.fromarray(out, "RGB")


if __name__ == "__main__":
    a = sys.argv[1:]
    src = Path(a[a.index("--in") + 1] if "--in" in a else HERE / "assets" / "map_base.png")
    th = int(a[a.index("--thresh") + 1]) if "--thresh" in a else 150
    dl = int(a[a.index("--dilate") + 1]) if "--dilate" in a else 3
    backup = src.with_suffix(".labelled.png")
    if not backup.exists():
        shutil.copy2(src, backup)
        print(f"kept the original at {backup.name}")
    strip(src, th, dl).save(src)
    print(f"wrote {src}")
