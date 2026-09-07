#!/usr/bin/env python3
"""
dropbox_auth.py — get the three Dropbox values the cloud build needs.

Only needed for [source] kind = "dropbox". Running the build locally against a
synced folder needs no Dropbox app at all.

GitHub's runners cannot see your machine, so a headless build needs its own way
to read the Fog of World folder. Dropbox access tokens expire after ~4 hours,
which is useless for a cron job; a *refresh* token does not expire, and the
build mints short-lived access tokens from it. This walks through getting one.

Before running, create the app (once, free):
  1. https://www.dropbox.com/developers/apps -> Create app
  2. "Scoped access" -> "App folder" -> name it anything
  3. Permissions tab -> tick files.metadata.read and files.content.read
     -> Submit.  (Do this BEFORE authorising, or the token lacks the scopes.)
  4. Settings tab -> copy the App key and App secret

Then:
    python dropbox_auth.py
"""
import sys
import json
import urllib.parse
import urllib.request

AUTH = "https://www.dropbox.com/oauth2/authorize"
TOKEN = "https://api.dropboxapi.com/oauth2/token"


def main():
    print(__doc__)
    key = input("App key:    ").strip()
    secret = input("App secret: ").strip()
    if not key or not secret:
        sys.exit("Both values are required.")

    url = f"{AUTH}?" + urllib.parse.urlencode({
        "client_id": key, "response_type": "code", "token_access_type": "offline"})
    print("\n1. Open this and click Allow:\n\n   " + url + "\n")
    code = input("2. Paste the access code shown: ").strip()
    if not code:
        sys.exit("No code entered.")

    data = urllib.parse.urlencode({
        "code": code, "grant_type": "authorization_code",
        "client_id": key, "client_secret": secret}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN, data=data), timeout=30) as r:
            tok = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"\nDropbox rejected the exchange ({e.code}): {e.read().decode()[:300]}\n"
                 "Codes are single-use and expire quickly — re-run and use a fresh one.")

    refresh = tok.get("refresh_token")
    if not refresh:
        sys.exit("No refresh_token returned. Make sure the authorise URL included "
                 "token_access_type=offline (this script adds it).")

    print("\nDone. Add these three under your repo's\n"
          "Settings -> Secrets and variables -> Actions -> New repository secret:\n")
    print(f"  DROPBOX_APP_KEY       {key}")
    print(f"  DROPBOX_APP_SECRET    {secret}")
    print(f"  DROPBOX_REFRESH_TOKEN {refresh}")
    print("\nThe refresh token does not expire. Treat it like a password: it grants "
          "read access to that Dropbox app folder.\n"
          "Scopes granted:", tok.get("scope", "(none reported)"))


if __name__ == "__main__":
    main()
