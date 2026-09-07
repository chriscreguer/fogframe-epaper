#!/usr/bin/env python3
"""Regression tests for the world delta archive."""
import shutil
import tempfile
from datetime import date
from pathlib import Path

import numpy as np

import geo
import worldlog as wl

BB = geo.BLOCK_BYTES


def _block(bits_set):
    g = np.zeros((64, 64), np.uint8)
    for y, x in bits_set:
        g[y, x] = 1
    return np.packbits(g, axis=1).reshape(BB)


def test_two_runs_same_day_keep_both():
    """A second run on the same day must not erase the first run's pixels."""
    d = Path(tempfile.mkdtemp())
    key = (100, 100, 0, 0)
    today = date(2026, 9, 7).isoformat()

    # run 1: two pixels appear
    first = {key: _block([(0, 0), (0, 1)])}
    wl.write_delta(wl.replay(d, before=today), first, today, d)
    after_run1 = int(np.unpackbits(wl.load_delta(d / f"{today}.npz")[1]).sum())

    # run 2, same day: one more pixel, first two still present upstream
    second = {key: _block([(0, 0), (0, 1), (0, 2)])}
    wl.write_delta(wl.replay(d, before=today), second, today, d)
    after_run2 = int(np.unpackbits(wl.load_delta(d / f"{today}.npz")[1]).sum())

    total = int(np.unpackbits(wl.replay(d)[key]).sum())
    shutil.rmtree(d)
    assert after_run1 == 2, f"run 1 recorded {after_run1}, expected 2"
    assert after_run2 == 3, f"run 2 left {after_run2} px in the day file, expected 3"
    assert total == 3, f"archive replays to {total} px, expected 3"


def test_prior_days_untouched():
    """Rewriting today must not disturb earlier days."""
    d = Path(tempfile.mkdtemp())
    key = (100, 100, 0, 0)
    wl.write_delta({}, {key: _block([(0, 0)])}, "2026-09-06", d)
    wl.write_delta(wl.replay(d, before="2026-09-07"),
                   {key: _block([(0, 0), (0, 1)])}, "2026-09-07", d)
    y = int(np.unpackbits(wl.load_delta(d / "2026-09-06.npz")[1]).sum())
    t = int(np.unpackbits(wl.load_delta(d / "2026-09-07.npz")[1]).sum())
    shutil.rmtree(d)
    assert y == 1, f"yesterday changed: {y}"
    assert t == 1, f"today should hold only its own new px, got {t}"


def test_monotonic_guard_catches_erasure():
    """A shrinking archive must fail loudly, not pass silently."""
    d = Path(tempfile.mkdtemp())
    key = (100, 100, 0, 0)
    wl.write_delta({}, {key: _block([(0, 0), (0, 1), (0, 2)])}, "2026-09-06", d)
    wl.write_meta(d, "test")
    n, _ = wl.check_monotonic(d)
    assert n == 3, f"expected 3 px, got {n}"

    # clobber the delta the way the bug did, then re-check
    wl.write_delta({}, {key: _block([(0, 0)])}, "2026-09-06", d)
    try:
        wl.check_monotonic(d)
    except SystemExit as e:
        shutil.rmtree(d)
        assert "REGRESSION" in str(e), f"wrong error: {e}"
        return
    shutil.rmtree(d)
    raise AssertionError("erasure was not caught")


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print(f"PASS {name}")
            except AssertionError as e:
                fails += 1; print(f"FAIL {name}: {e}")
            except TypeError as e:
                fails += 1; print(f"FAIL {name}: {e}")
    raise SystemExit(fails)
