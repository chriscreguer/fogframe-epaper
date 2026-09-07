#!/usr/bin/env python3
"""
build.py — full daily pipeline, run by GitHub Actions (or locally).

  1. Resolve the Fog of World data source              (sources)
  2. Append today's delta to the world archive         (snapshot)
  3. Render the fog map to RGB                          (render_fog)
  4. Quantize to 6 colours and pack to frame.bin        (pack_spectra6)

Published outputs (committed by CI, served via raw.githubusercontent):
    device/frame.bin     — what the ESP32 fetches
    device/preview.png    — human-viewable preview

Local data override: pass --data <dir> (a folder containing Sync) to skip the
Dropbox download and use a local copy instead.

Usage:
    python build.py                 # cloud: pull from Dropbox
    python build.py --data "/path/to/Fog of World"
"""
import sys
import tempfile
from pathlib import Path

import sources
import render_fog
import pack_spectra6
import snapshot

HERE = Path(__file__).parent
BUILD = HERE / "build"
DEVICE = HERE / "device"


def main(data_dir=None):
    data_dir = sources.resolve(data_dir)
    snapshot.update(data_dir)

    BUILD.mkdir(exist_ok=True)
    DEVICE.mkdir(exist_ok=True)
    rgb = BUILD / "fog_rgb.png"
    render_fog.render(data_dir, rgb)
    pack_spectra6.main(str(rgb), str(DEVICE / "frame.bin"), str(DEVICE / "preview.png"))
    print("Pipeline complete.")


if __name__ == "__main__":
    data = None
    if "--data" in sys.argv:
        data = sys.argv[sys.argv.index("--data") + 1]
    main(data)
