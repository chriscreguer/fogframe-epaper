#!/usr/bin/env python3
"""
dropbox_pull.py — download the Fog of World "Sync" folder from Dropbox.

Uses a long-lived refresh token (offline OAuth) to mint a short-lived access
token, then downloads every file under <FOLDER>/Sync into <dest>/Sync.
Standard library only (no Dropbox SDK) so CI needs no extra deps.

Credentials are read from environment variables:
    DROPBOX_APP_KEY
    DROPBOX_APP_SECRET
    DROPBOX_REFRESH_TOKEN

Usage:
    python dropbox_pull.py <dest_dir>   # creates <dest_dir>/Sync
"""
import os
import sys
import json
import logging
import urllib.parse
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S", stream=sys.stdout)
log = logging.getLogger("dropbox_pull")

from config import CFG

FOLDER = CFG["source"]["dropbox_folder"]
API = "https://api.dropboxapi.com"
CONTENT = "https://content.dropboxapi.com"


def _access_token():
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": os.environ["DROPBOX_REFRESH_TOKEN"],
        "client_id": os.environ["DROPBOX_APP_KEY"],
        "client_secret": os.environ["DROPBOX_APP_SECRET"],
    }).encode()
    req = urllib.request.Request(f"{API}/oauth2/token", data=data)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())["access_token"]


def _list_sync(token):
    """Yield file entries under FOLDER/Sync, following pagination."""
    def post(path, body):
        req = urllib.request.Request(
            f"{API}{path}", data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=60).read())

    res = post("/2/files/list_folder", {"path": f"{FOLDER}/Sync", "recursive": True})
    while True:
        for e in res["entries"]:
            if e[".tag"] == "file":
                yield e
        if not res.get("has_more"):
            break
        res = post("/2/files/list_folder/continue", {"cursor": res["cursor"]})


def _download(token, dropbox_path, dest):
    req = urllib.request.Request(
        f"{CONTENT}/2/files/download",
        headers={"Authorization": f"Bearer {token}",
                 "Dropbox-API-Arg": json.dumps({"path": dropbox_path})})
    with urllib.request.urlopen(req, timeout=120) as r:
        dest.write_bytes(r.read())


def pull(dest_dir):
    token = _access_token()
    sync_dest = Path(dest_dir) / "Sync"
    sync_dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for e in _list_sync(token):
        # path_lower like /apps/fog of world/sync/<name>; keep just the leaf
        name = e["name"]
        _download(token, e["path_lower"], sync_dest / name)
        n += 1
    log.info(f"Downloaded {n} tiles → {sync_dest}")
    if n == 0:
        raise RuntimeError("No tiles downloaded — check token scopes / folder path")
    return str(dest_dir)


if __name__ == "__main__":
    dest = sys.argv[1] if len(sys.argv) > 1 else "fow_data"
    pull(dest)
