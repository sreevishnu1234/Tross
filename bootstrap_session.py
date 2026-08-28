"""
One-time manual step to give the pure-HTTP API a working session.

LinkedIn's login endpoint fingerprint-blocks non-browser clients (see README),
so the automated credentials-based login can't reliably clear it. This script
takes the li_at/JSESSIONID cookies a normal, manual browser login already
produced and saves them in the flat format app/scraper.py expects — no
browser is launched or automated by this script itself.

Usage:
    1. Log into the dummy LinkedIn account in an ordinary browser.
    2. DevTools -> Application -> Cookies -> https://www.linkedin.com
    3. Copy the values of "li_at" and "JSESSIONID".
    4. Run this script and paste them in when prompted.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from app.config import SESSION_STATE_PATH  # noqa: E402


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

    li_at = input("li_at: ").strip()
    jsessionid = input("JSESSIONID: ").strip()

    if not li_at or not jsessionid:
        raise SystemExit("Both li_at and JSESSIONID are required.")

    path = session_path_for(args.account)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"li_at": li_at, "JSESSIONID": jsessionid}, f)

    print(f"Saved {path}")


if __name__ == "__main__":
    main()
