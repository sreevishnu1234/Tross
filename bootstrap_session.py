"""
One-time manual step to give the pure-HTTP API a working session.

LinkedIn's login endpoint fingerprint-blocks non-browser clients (see README),
so the automated credentials-based login can't reliably clear it. This script
takes the full Cookie header a normal, manual browser login already produced
and saves it in the flat format app/scraper.py expects — no browser is
launched or automated by this script itself.

Copying the *whole* cookie header (not just li_at/JSESSIONID individually)
matters: replaying only a couple of cookies without the rest of the browser's
original set looks like a different device reusing a stolen session token to
LinkedIn's fraud detection, and gets rejected even when li_at itself is
genuinely fresh and valid.

Usage:
    1. Log into the dummy LinkedIn account in an ordinary browser.
    2. DevTools -> Network tab -> reload linkedin.com/feed.
    3. Click any request to linkedin.com -> Headers -> find the "Cookie"
       request header and copy its entire value.
    4. Run this script and paste it in when prompted.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from app.config import SESSION_STATE_PATH  # noqa: E402
from app.scraper import _parse_cookie_header  # noqa: E402


def session_path_for(index: int) -> str:
    if index == 0:
        return SESSION_STATE_PATH
    root, ext = os.path.splitext(SESSION_STATE_PATH)
    return f"{root}_{index}{ext}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--account",
        type=int,
        default=0,
        help="Index into LINKEDIN_ACCOUNTS to bootstrap (0 = first account, default).",
    )
    args = parser.parse_args()

    cookie_header = input("Cookie header: ").strip()
    if not cookie_header:
        raise SystemExit("The Cookie header value is required.")

    cookies = _parse_cookie_header(cookie_header)
    if not cookies.get("li_at") or not cookies.get("JSESSIONID"):
        raise SystemExit("That doesn't look like a full Cookie header — li_at and JSESSIONID must both be present.")

    path = session_path_for(args.account)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cookies, f)

    print(f"Saved {path} ({len(cookies)} cookies)")


if __name__ == "__main__":
    main()
