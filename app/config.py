import os

from dotenv import load_dotenv

load_dotenv()

# one or more "username:password" pairs, comma-separated — if the first
# account's login fails (checkpoint, wrong password, banned), the scraper
# automatically fails over to the next one
_accounts_raw = os.getenv("LINKEDIN_ACCOUNTS", "")
if _accounts_raw:
    LINKEDIN_ACCOUNTS = []
    for pair in _accounts_raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        username, _, password = pair.partition(":")
        LINKEDIN_ACCOUNTS.append((username.strip(), password.strip()))
else:
    # backward-compatible single-account form
    username = os.getenv("LINKEDIN_USERNAME", "")
    password = os.getenv("LINKEDIN_PASSWORD", "")
    LINKEDIN_ACCOUNTS = [(username, password)] if username and password else []

REQUEST_TIMEOUT_MS = int(os.getenv("REQUEST_TIMEOUT_MS", "30000"))
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "5"))

# where each account's logged-in session (cookies + local storage) is cached on
# disk so the app doesn't have to log in again every time the process restarts.
# Treat these files like a live password — never commit them.
SESSION_STATE_PATH = os.getenv("SESSION_STATE_PATH", "session_state.json")
KEEP_ALIVE_INTERVAL_SECONDS = int(os.getenv("KEEP_ALIVE_INTERVAL_SECONDS", str(6 * 3600)))

if not LINKEDIN_ACCOUNTS:
    raise RuntimeError(
        "No LinkedIn accounts configured. Set LINKEDIN_ACCOUNTS=user1:pass1,user2:pass2 "
        "(or the older LINKEDIN_USERNAME / LINKEDIN_PASSWORD pair) in your .env file "
        "locally or in your hosting platform's env vars."
    )
