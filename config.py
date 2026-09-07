#!/usr/bin/env python3
"""
config.py — load config.toml (falling back to config.example.toml).

Every tunable lives in one file so the code itself stays generic. Modules
import the values they need and expose them under their original names, so
the rest of the pipeline reads exactly as it did with hardcoded constants.
"""
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).parent
CONFIG_PATH  = HERE / "config.toml"
EXAMPLE_PATH = HERE / "config.example.toml"


def _load():
    path = CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_PATH
    if not path.exists():
        sys.exit(f"No config found. Copy {EXAMPLE_PATH.name} to config.toml and edit it.")
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    if path is EXAMPLE_PATH:
        print(f"[config] using {EXAMPLE_PATH.name} — copy it to config.toml to customise",
              file=sys.stderr)
    _validate(cfg)
    return cfg


def _validate(cfg):
    m = cfg["map"]
    if m["north"] <= m["south"]:
        sys.exit("config [map]: north must be greater than south")
    if m["east"] <= m["west"]:
        sys.exit("config [map]: east must be greater than west (antimeridian is unsupported)")
    for k in ("north", "south"):
        if not -85.05 <= m[k] <= 85.05:
            sys.exit(f"config [map]: {k} is outside the Web Mercator range")
    c = cfg["canvas"]
    if c["width"] % 2:
        sys.exit("config [canvas]: width must be even (the panel packs 2 px per byte)")


CFG = _load()

DATA_DIR = Path(CFG["source"]["path"]).expanduser()
