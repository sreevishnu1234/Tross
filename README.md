# LinkedIn Profile API

Give it a LinkedIn profile URL, get back structured JSON. **Zero browser involved** — the API
talks to LinkedIn's own internal REST endpoints directly over plain HTTP, the same way its
official web/mobile clients do.

**Confirmed working end-to-end**, including login/session, against live LinkedIn data — not
just in isolated tests, through the actual deployed UI. See Login and Known Limitations below
for exactly what that took and how fragile it still is by nature of what LinkedIn actively
does to prevent it.

## Live Deployment

- **API** (Render): https://tross-hieo.onrender.com
- **UI** (Streamlit Community Cloud): https://tross-linkden.streamlit.app/

Render's free tier spins down after inactivity, so the first request after a while may take
~30–60s to wake it back up. The UI's single form (`li_at` + `JSESSIONID` + profile URL) needs
a fresh cookie pair from a real browser login each time — see Login and Known Limitations.

## Setup

**Requires Python 3.12** (pinned in `.python-version`) — newer versions like 3.13/3.14 don't
yet have prebuilt wheels for `pydantic-core`, which forces pip to compile it from source and
fails on machines without a Rust/MSVC toolchain installed.

1. Clone the repo and install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env`. `LINKEDIN_ACCOUNTS` is optional — it's only used as an
   automatic fallback if the active session ever needs to silently re-login on its own;
   day-to-day login happens through the UI (see step 4), so placeholder values are fine here.
3. Run the API:
   ```bash
   python run.py
   ```
4. Run the UI in a second terminal:
   ```bash
   streamlit run streamlit_app.py
   ```
   Set `API_URL` if the API isn't running on `http://127.0.0.1:8000`. Open the UI: the main
   form asks for `li_at`, `JSESSIONID`, and a profile URL together, and does the whole flow —
   session setup and the lookup — in one request (see Login for why these are combined rather
   than being two separate steps). Get the cookie values by logging into the account in an
   ordinary browser, then DevTools → Application → Cookies → `https://www.linkedin.com`. An
   "Advanced" section below it exposes automated credential login and a session-only bootstrap
   separately, for anyone who wants to reuse one saved session across multiple lookups instead
   — that path saves `session_state.json`, which the API reuses on every subsequent request
   and restart. **Treat that file like a live password — it's gitignored, and must never be
   committed or shared.**

## Deployment

Live at the URLs in "Live Deployment" above. To redeploy your own copy:

**API** — deploy to [Render](https://render.com) as a native Python web service:
- Set `PYTHON_VERSION` to `3.12.10` in the dashboard's environment variables.
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Set `LINKEDIN_ACCOUNTS` as an environment variable in the dashboard (placeholder values are
  fine — see Setup) — never real credentials in the repo.
- Bootstrap a session against the deployed instance via the UI's main form or the "Advanced"
  section (point its `API_URL` at the deployed API first) to give it a working session.

**UI** — deploy `streamlit_app.py` on [Streamlit Community Cloud](https://streamlit.io/cloud)
for free. In its advanced settings, pick Python 3.12. Set `API_URL` in its app secrets to
your deployed API's public URL — **not** the Streamlit app's own URL (an easy mistake:
Streamlit Cloud only hosts the UI, it can't also expose a separate callable API).

## API

### `GET /session/status`

Returns `{"configured": true|false}` — whether a session file exists for the active account
(doesn't confirm it's still valid against LinkedIn; a `/profile` call will reveal that).

### `POST /session/login`

Logs in directly via LinkedIn's HTTP API using a specific account's credentials — no browser.
```json
{ "username": "dummy@example.com", "password": "..." }
```
`200` on success. `401` if LinkedIn checkpoints the login or the credentials are wrong (see
Known Limitations — this has been observed on every live attempt so far).

### `POST /session/bootstrap`

Fallback for when `/session/login` gets checkpointed: accepts a session cookie pair obtained
by logging into the dummy account once in an ordinary browser (see Setup).
```json
{ "li_at": "...", "jsessionid": "..." }
```
`200` if the cookies check out against LinkedIn, `401` if they don't.

### `POST /session/bootstrap-and-scrape` — the recommended way to call this API

Does session bootstrap and a profile lookup as one continuous back-to-back request sequence
server-side, instead of two separate calls with an unpredictable gap between them. This is the
form the UI leads with, and the one that's actually been confirmed working end-to-end — see
Login for why the gap between "save session" and "look up a profile" matters here.
```json
{ "li_at": "...", "jsessionid": "...", "url": "https://www.linkedin.com/in/some-person/" }
```
Returns the same `ProfileResponse` shape as `/profile` below on success.

### `POST /profile`

Uses whatever session is already saved (from `/session/login`, `/session/bootstrap`, or a
previous `/session/bootstrap-and-scrape` call) rather than taking a cookie directly — prefer
`/session/bootstrap-and-scrape` unless you specifically need to reuse an existing session
across multiple profile lookups.

**Request**
```json
{ "url": "https://www.linkedin.com/in/some-person/" }
```

**Response** (`200`)
```json
{
  "name": "Jane Doe",
  "headline": "Software Engineer at Example Corp",
  "location": "US",
  "about": "...",
  "experience": [
    { "title": "Software Engineer", "company": "Example Corp", "duration": "Jan 2021 - Present", "location": "San Francisco", "description": "Building things." }
  ],
  "education": [
    { "school": "State University", "degree": "B.S., Computer Science", "duration": "2017 - 2021" }
  ],
  "skills": ["Python", "System Design"],
  "certifications": ["AWS Certified Developer"],
  "languages": ["English (Native or bilingual proficiency)"],
  "profile_image_url": "https://media.licdn.com/..."
}
```

Every field is confirmed working against LinkedIn's real JSON API — `name`, `headline`,
`about`, `profile_image_url`, `experience`, `education`, `certifications`, `skills`, and
`languages` were all verified against live responses during development (see Approach).
`location` is the one exception worth knowing about: it's a country code (e.g. `"IN"`), not a
full "City, Region, Country" string — see Known Limitations.

**Example curl**
```bash
curl -X POST https://your-deployed-url/profile \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.linkedin.com/in/some-person/"}'
```

**Errors**
| Status | Meaning |
|---|---|
| 400 | URL isn't a valid LinkedIn profile URL |
| 401 | Session expired/invalid, login failed, or LinkedIn threw up a checkpoint |
| 404 | Profile is private, restricted, or doesn't exist |
| 429 | You've hit this API's own rate limit |
| 500 | Server misconfigured, or an unexpected error occurred |
| 502/504 | LinkedIn's own API didn't respond as expected |

### `GET /health`

Basic liveness check, returns `{"status": "ok"}`.

## Approach

This calls LinkedIn's internal Voyager REST API directly — no Playwright, no Selenium, no
headless Chromium, no DOM rendering or scraping of any kind. `requirements.txt` has no
browser dependency at all. Authentication is a `li_at` session cookie plus a `csrf-token`
header set to the (unquoted) `JSESSIONID` cookie value — the same pattern real
reverse-engineered LinkedIn clients use.

Requests are made with [`curl_cffi`](https://github.com/lexiforest/curl_cffi) instead of a
plain HTTP client. This matters for a reason discovered directly during development: LinkedIn
fingerprints the *TLS handshake itself*, not just headers. A request sent with a generic
Python HTTP client's TLS signature got the account's session revoked instantly — and since
`li_at` isn't a copy of the session but the session itself, that revocation logged the account
out of its real browser tab too, not just "our" copy. `curl_cffi` makes the outgoing TLS
handshake match a real Chrome browser's, which is what actually stopped this from happening.

**Endpoints used**, all under `https://www.linkedin.com/voyager/api/identity/dash/`, called
with `?q=viewee&profileUrn=<urn>` (the profile's own `entityUrn`, read from the first call's
response) except `profiles` itself, which is looked up by vanity name:

| Endpoint | Confirmed against live data? |
|---|---|
| `profiles?q=memberIdentity&memberIdentity=<vanity-name>` | Yes — name, headline, about, photo, country |
| `profileEducations` | Yes — school, degree, field of study, dates, activities |
| `profileCertifications` | Yes — name, authority, license number, dates, URL |
| `profileSkills` | Yes — clean skill names |
| `profilePositions` | Yes — title, company, dates, location, description (12/12 roles on a real profile, correctly split even when multiple roles shared one employer) |
| `profileLanguages` | Yes — name + proficiency level |

None of these endpoint names are documented anywhere — they were found by testing plausible
Voyager naming patterns (`profile<PluralNoun>`) directly against LinkedIn and reading the
response: a `200` with real JSON confirms it, a `404` means the route doesn't exist, and
(this was the key trick for further recon once the accounts on hand started getting
rate-limited) **a `401` returned even with a completely fake, garbage `li_at` cookie means
the route exists and is just correctly demanding real auth** — which makes it possible to
keep searching for endpoint names without touching any real account. That's how
`profilePositions` and `profileLanguages` were both confirmed real (and later confirmed
*correct*, field names and all, against live data) without spending extra requests on
already-flagged accounts.

### Login

`app/scraper.py` implements a fully backend-driven, credentials-based login
(`POST /uas/login-submit`, picking up the CSRF token from the cookie the login page itself
sets) exposed both automatically (whenever a request needs a session and none is saved) and
on-demand via `POST /session/login` / the UI's login form. It was tested live, multiple times
with multiple real accounts, and every attempt got checkpointed by LinkedIn's fraud system
before a session was issued — see Known Limitations. `POST /session/bootstrap` (and the UI's
matching fallback form) exists for exactly this case: it accepts a `li_at`/`JSESSIONID` pair
obtained by logging into the dummy account once in an ordinary browser — not scripted, not
automated by this codebase — and adopts it if LinkedIn confirms it's valid.

**Getting a bootstrapped session to actually hold up took real trial and error.** Early
attempts sent `li_at`/`JSESSIONID` more or less as copied and got the session revoked by
LinkedIn on the very next request, every time (see Known Limitations for the raw evidence).
Three things, found through direct testing, turned that into a session that reliably serves
real data:

1. **`JSESSIONID` must be sent quoted** in the `Cookie` header (`JSESSIONID="ajax:..."`) —
   LinkedIn's own browser client always sends it that way; replaying it bare was one of the
   things that made an otherwise-valid, freshly-issued cookie look illegitimate.
2. **A priming `GET /feed/` before the first real API call.** Hitting the Voyager API cold, as
   the very first request on a connection, looked automated even with valid cookies. One cheap
   request to the feed page first fixed that.
3. **Every call after that must reuse one persistent connection** instead of each opening a
   new one — and the section calls (`profilePositions`, `profileEducations`, etc.) need to
   fire *concurrently*, not one at a time with gaps between them. A slow sequential trickle of
   requests got the session killed partway through even with everything else correct; the same
   requests fired as a burst — matching how a real browser's page load actually looks, several
   XHR calls firing at once rather than one-by-one — went through cleanly.

`app/scraper.py` implements all three: `_get_http_session()` keeps one connection alive for
the lifetime of a bootstrapped session, `_prime_session()` runs before anything else, and
`scrape_profile()`'s `asyncio.gather` over the section endpoints was already concurrent by
design — it just needed the connection-reuse and priming pieces alongside it to actually stay
authenticated for those calls to land.


## Known Limitations

- Scraping/querying LinkedIn's internal API goes against their Terms of Service — that's
  inherent to this approach, not a bug.
- **LinkedIn's automated (credentials-based) login is blocked in practice, repeatedly and
  consistently.** It's fully implemented and is what's attempted first, but it was
  checkpointed on every live attempt during development, across multiple different accounts
  (including a personal one, tested once at the account owner's explicit request). Every
  account needs the manual cookie flow (`/session/bootstrap-and-scrape`, or `/session/
  bootstrap` — see the Login section above) instead — this path is the one confirmed working.
- **A bootstrapped session only stays valid for a short, specific pattern of requests, and
  getting that pattern wrong revokes it outright — including in ways that got a real account
  restricted and asked to submit a government ID to regain access.** Early attempts sent
  `li_at`/`JSESSIONID` unquoted, cold (no priming request), and one call at a time from a fresh
  connection each time; every one of those got the session revoked, and the server's own
  response showed why:
  ```
  set-cookie: li_at=delete me; ...Max-Age=0...
  clear-site-data: "storage"
  ```
  LinkedIn isn't silently blocking these requests — it's actively instructing the client to
  delete the cookie, i.e. deliberately revoking the session, consistent with PerimeterX's
  behavioral bot-detection layer. The three fixes in the Login section above (quoted
  `JSESSIONID`, a priming request, one persistent connection with the section calls fired
  concurrently) resolved this and were confirmed, live, to produce a session that serves real
  data end-to-end through the actual deployed UI — but this cost real accounts along the way
  to find, up to and including one full account restriction. A session bootstrapped this way
  still only survives a handful of requests before LinkedIn's fraud detection catches up to
  it, which is why `/session/bootstrap-and-scrape` (cookie + profile URL together, one call)
  is the recommended endpoint over bootstrapping and looking up a profile as two separate
  steps — anything that introduces a gap between them reintroduces the risk of revocation.
  Anyone re-testing this should expect the same risk and treat it accordingly — testing this
  project's own fixes is what triggered the account restriction described above.
- **A verified account appears to survive this better than a brand-new or already-flagged
  one.** Every fresh/dummy account tried during development got flagged or restricted
  quickly; the account that finally produced a clean, repeatable, working result was one with
  an established identity-verification history. Not conclusively proven, but consistent with
  everything observed — LinkedIn's fraud system plausibly trusts a verified account's session
  more than a brand-new one's, independent of anything this codebase does differently.
- There is no session that can be made to literally never expire — `li_at` has a real expiry
  set by LinkedIn, and LinkedIn can invalidate a session early at its own discretion. When
  that happens, the automated login is retried first; if it's checkpointed (the observed
  common case), the account needs re-authenticating manually via the UI.
- **Every account used during this project's development is now rate-limited or restricted.**
  Multi-account failover (`LINKEDIN_ACCOUNTS`) adds redundancy against one account tripping a
  limit, but all accounts still call from the same server/IP, which LinkedIn's fraud detection
  can correlate — it is not a way to avoid this constraint, only to spread it out.
- Private profiles, or profiles LinkedIn otherwise restricts for the configured account,
  return `404` rather than partial data.
- No retry/backoff queue beyond one built-in retry on a transient network failure — a
  persistent block requires the caller to retry later, or the account to be re-authenticated.
