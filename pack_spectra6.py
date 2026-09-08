#!/usr/bin/env python3
"""
pack_spectra6.py — quantize an RGB image to the 6 E Ink Spectra colors and
pack it into the panel wire format for the 13.3" E6 (1200x1600).

Outputs:
  frame.bin    — 960,000 bytes. 4 bits/pixel, 2 pixels/byte, row-major.
                 High nibble = even (left) pixel. Nibble = the panel's RAW E6
                 colour code, so the firmware streams it straight into the
                 Seeed_GFX 4bpp sprite buffer (_img8) with no remapping:
                   0x0=WHITE 0x2=GREEN 0x6=RED 0xB=YELLOW 0xD=BLUE 0xF=BLACK
                 (matches Seeed_GFX dither.h PAL_E6 raw codes for T133A01).
  preview.png  — the quantized result as RGB, so you can see what the panel
                 will show before flashing.

Usage:
    python pack_spectra6.py <in_rgb.png> [frame.bin] [preview.png]
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

W, H = 1200, 1600

# Index order is the wire contract with the firmware. Do not reorder.
from config import CFG

PALETTE = [
    (0,   0,   0),     # 0 BLACK
    (255, 255, 255),   # 1 WHITE
    (228, 212, 34),    # 2 YELLOW
    (172, 38,  38),    # 3 RED
    (44,  52,  128),   # 4 BLUE
    (58,  116, 70),    # 5 GREEN
]


def _palette_image():
    pal = Image.new("P", (1, 1))
    flat = []
    for rgb in PALETTE:
        flat += list(rgb)
    flat += [0, 0, 0] * (256 - len(PALETTE))  # pad to 256 entries
    pal.putpalette(flat)
    return pal


# 8x8 Bayer matrix (normalised 0..1) for ordered black/white dithering of neutrals.
_BAYER8 = np.array([
    [0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38], [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21],
], dtype=np.float32) / 64.0

# Unexplored ground lands in a narrow band around mid grey, and an ordered
# dither renders that band as a checkerboard PLUS a sparse 8 px lattice of
# extra white pixels wherever the tone sits above exactly 50%. That lattice
# carries almost no tone but reads as a grid of bright dots on e-paper.
# Snapping the band to a flat checkerboard removes it: even grey, no grid, no
# noise. Widen or narrow it if your fog opacity differs from the default.
FLAT_LO       = CFG["panel"]["flat_lo"]
FLAT_HI       = CFG["panel"]["flat_hi"]

PARK_HUE_LO   = CFG["park"]["hue_lo"]
PARK_HUE_HI   = CFG["park"]["hue_hi"]

SAT_BOOST     = CFG["panel"]["sat_boost"]      # amplify the muted base-map colours before classifying
SAT_THRESHOLD = CFG["panel"]["sat_threshold"]  # (on boosted image) below this -> neutral black/white

ROTATE_180    = True   # panel is mounted upside down relative to native orientation


def quantize(rgb_img):
    """RGB image -> (H,W) uint8 palette indices 0..5.

    Saturation-aware: muted base-map colours are first boosted, then neutral
    (low-chroma) pixels are ordered-dithered between BLACK and WHITE by
    luminance — clean fog/street texture with no colour cast. Chromatic pixels
    snap to the nearest of YELLOW/RED/BLUE/GREEN. This avoids PIL's failure mode
    where mid-greys collapse onto the blue palette entry.
    """
    if rgb_img.size != (W, H):
        rgb_img = rgb_img.resize((W, H), Image.LANCZOS)
    rgb_img = rgb_img.convert("RGB")

    # Luminance from the ORIGINAL (unboosted) image for clean B/W dithering.
    rgb0 = np.asarray(rgb_img, dtype=np.float32)
    lum = (0.299 * rgb0[:, :, 0] + 0.587 * rgb0[:, :, 1] + 0.114 * rgb0[:, :, 2]) / 255.0

    # Boost saturation, then classify chroma on the boosted image.
    hsv = np.asarray(rgb_img.convert("HSV"), dtype=np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * SAT_BOOST, 0, 255)
    hue = hsv[:, :, 0]   # 0..255 (PIL); parks≈48-64, water/cyan≈112-128
    rgb = np.asarray(Image.fromarray(hsv.astype(np.uint8), mode="HSV").convert("RGB"),
                     dtype=np.float32)

    maxc = rgb.max(axis=2)
    minc = rgb.min(axis=2)
    sat = (maxc - minc) / (maxc + 1e-6)
    chromatic = sat >= SAT_THRESHOLD

    # Neutral pixels -> ordered B/W dither.
    thresh = np.tile(_BAYER8, (H // 8 + 1, W // 8 + 1))[:H, :W]
    out = np.where(lum > thresh, 1, 0).astype(np.uint8)   # 1=WHITE, 0=BLACK

    # Flat mid-grey -> exact checkerboard, the most even 50% a 1-bit panel can show
    yy, xx = np.indices((H, W))
    flat = (lum >= FLAT_LO) & (lum <= FLAT_HI)
    out[flat] = ((yy + xx) % 2).astype(np.uint8)[flat]

    # Base classification: nearest of RED (recent trips/arterials) or GREEN (parks).
    # Yellow stays excluded (the muted base misfires green parkland to yellow).
    CHROMA_IDX = [3, 5]                                            # RED, GREEN
    chroma_pal = np.array([PALETTE[i] for i in CHROMA_IDX], dtype=np.float32)
    diff = rgb[:, :, None, :] - chroma_pal[None, None, :, :]       # (H,W,2,3)
    pick = np.argmin((diff ** 2).sum(axis=3), axis=2)             # 0..1
    nearest = np.array(CHROMA_IDX, dtype=np.uint8)[pick]          # ->3 or 5

    # Only assign RED/GREEN within their real hue bands. Without this, any
    # moderately-saturated warm neutral (sand, tan parking lots, beach
    # structures) has nowhere to go but the nearest of the two and always
    # lands on RED (nearest-colour has no "neutral" option) — e.g. Montrose
    # Beach's tan boathouse/parking area painting solid red though it was
    # never actually recently explored. Real trip-red is essentially always
    # hue==0 (a flat painted overlay colour); real park green sits ~48-64.
    red_hue = (hue <= 10) | (hue >= 245)
    green_hue = (hue >= PARK_HUE_LO) & (hue <= PARK_HUE_HI)
    assign = chromatic & (red_hue | green_hue)
    out[assign] = nearest[assign]

    # Water override: lake/river read as teal-cyan (hue ~112-128), which sits
    # between the green and dark-navy blue palette entries, so nearest-colour
    # grabs green. Force the cyan band to BLUE. Parks (hue ~48-64) are untouched.
    water = chromatic & (hue > PARK_HUE_HI) & (hue <= 175)
    out[water] = 4                                                 # BLUE
    return out


# Map palette index (0..5) -> raw T133A01 E6 nibble code.
#                BLACK WHITE YELLOW RED  BLUE  GREEN
E6_CODE = np.array([0xF, 0x0, 0xB, 0x6, 0xD, 0x2], dtype=np.uint8)


def pack(indices):
    """(H,W) indices 0..5 -> bytes of RAW E6 codes, 2 px/byte, high nibble=even x."""
    assert indices.shape == (H, W)
    codes = E6_CODE[indices]
    hi = codes[:, 0::2]          # even columns -> high nibble
    lo = codes[:, 1::2]          # odd columns  -> low nibble
    packed = ((hi << 4) | lo).astype(np.uint8)
    return packed.tobytes()


def preview(indices):
    lut = np.array(PALETTE, dtype=np.uint8)
    return Image.fromarray(lut[indices], mode="RGB")


def main(in_path, bin_path, prev_path):
    img = Image.open(in_path)
    idx = quantize(img)
    # The panel is mounted upside down, so the buffer is rotated for it — but
    # the preview is for a person, so it keeps the readable orientation.
    panel_idx = np.rot90(idx, 2) if ROTATE_180 else idx
    counts = np.bincount(idx.ravel(), minlength=6)
    names = ["BLACK", "WHITE", "YELLOW", "RED", "BLUE", "GREEN"]
    total = idx.size
    print("Spectra6 distribution:")
    for n, c in zip(names, counts):
        print(f"  {n:6s} {c:9,d}  {100*c/total:5.1f}%")

    data = pack(panel_idx)
    Path(bin_path).parent.mkdir(parents=True, exist_ok=True)
    Path(bin_path).write_bytes(data)
    print(f"Wrote {bin_path} ({len(data):,} bytes)")

    preview(idx).save(prev_path)
    print(f"Wrote {prev_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    here = Path(__file__).parent
    in_path = sys.argv[1]
    bin_path = sys.argv[2] if len(sys.argv) > 2 else str(here / "build" / "frame.bin")
    prev_path = sys.argv[3] if len(sys.argv) > 3 else str(here / "build" / "preview.png")
    main(in_path, bin_path, prev_path)
