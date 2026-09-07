#!/usr/bin/env python3
"""
snapshot.py — record today's exploration into the world delta archive.

Appends a whole-world delta to world/ (see worldlog.py): only the blocks that
changed, and only the bits that turned on. A typical day costs well under a
kilobyte and covers the entire planet, not just the frame's bbox.

After appending, the archive is checked for regression: replay is additive,
so its pixel count can only grow, and a drop means a delta was clobbered.

Reads the Fog of World database; never writes to it.

Usage:
    python snapshot.py <fow_data_dir>
"""
import sys
import logging
from datetime import date
from pathlib import Path

import numpy as np

import geo
import worldlog as wl

HERE = Path(__file__).parent
WORLD_LOG_DIR = HERE / "world"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S", stream=sys.stdout)
log = logging.getLogger("snapshot")


def update_world_log(data_dir, today, log_dir=WORLD_LOG_DIR, database_id="primary"):
    """Append today's whole-world delta. Returns the path, or None if nothing new."""
    log_dir = Path(log_dir)
    # `before=today` is load-bearing: replaying today's own delta into `prev`
    # would make a second run of the day rewrite that file with only the
    # increment, erasing what the first run recorded.
    prev = wl.replay(log_dir, before=str(today)) if log_dir.exists() else {}
    cur = wl.scan(data_dir)
    added, removed = wl.diff(prev, cur)
    n_add = sum(int(np.unpackbits(b).sum()) for _, b in added)
    n_rem = sum(int(np.unpackbits(b).sum()) for _, b in removed)
    if n_rem:
        log.warning(f"{n_rem:,} px present yesterday are gone today — "
                    f"recorded, but replay stays additive")
    path = wl.write_delta(prev, cur, today, log_dir)
    if path is None:
        log.info("World log: no change today")
        return None
    if not (log_dir / "meta.json").exists():
        wl.write_meta(log_dir, database_id)
    log.info(f"World log: +{n_add:,} px across {len(added)} blocks "
             f"→ {path.name} ({path.stat().st_size/1024:.1f} KB)")
    now, was = wl.check_monotonic(log_dir)
    log.info(f"Archive: {now:,} px worldwide" + (f" (was {was:,})" if was else ""))
    return path


def update(data_dir):
    update_world_log(data_dir, date.today())


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    update(sys.argv[1])
