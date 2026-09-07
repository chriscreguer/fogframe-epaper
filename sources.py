#!/usr/bin/env python3
"""
sources.py — resolve where the Fog of World data comes from.

"local"   a folder containing Sync/ (a Dropbox/iCloud folder synced by the app,
          or a manual export). Nothing is downloaded.
"dropbox" pulled with a refresh token, for headless/CI runs.

Both paths are read-only with respect to your data.
"""
import sys
import tempfile
from pathlib import Path

from config import CFG, DATA_DIR


def resolve(data_dir=None):
    if data_dir:
        return str(Path(data_dir).expanduser())

    kind = CFG["source"]["kind"]
    if kind == "local":
        d = DATA_DIR
        if not (d / "Sync").is_dir():
            sys.exit(f"No Sync/ folder under {d}. Fix [source] path in config.toml.")
        return str(d)
    if kind == "dropbox":
        import dropbox_pull
        return dropbox_pull.pull(tempfile.mkdtemp(prefix="fow_"))
    sys.exit(f'Unknown [source] kind "{kind}" — use "local" or "dropbox".')
