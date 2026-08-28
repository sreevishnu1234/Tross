# LinkedIn Profile API

Give it a LinkedIn profile URL, get back structured JSON. **Zero browser involved** — the API
talks to LinkedIn's own internal REST endpoints directly over plain HTTP, the same way its
official web/mobile clients do.

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
   Set `API_URL` if the API isn't running on `http://127.0.0.1:8000`. Open the UI and use the
   **"1. Session"** section to log in with a throwaway/dummy account's email + password —
   not your personal account. This calls the API's own `/session/login`, which authenticates
   directly against LinkedIn over plain HTTP (see Approach). If LinkedIn checkpoints that
   login (a real, observed possibility — see Known Limitations), the same section has a
   fallback: log into the dummy account once in an ordinary browser and paste its `li_at` /
   `JSESSIONID` cookie values in instead. Either path saves `session_state.json`, which the
   API reuses on every subsequent request and restart. **Treat this file like a live
   password — it's gitignored, and must never be committed or shared.**

## Deployment

**API** — deploy to [Render](https://render.com) as a native Python web service:
- Set `PYTHON_VERSION` to `3.12.10` in the dashboard's environment variables.
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Set `LINKEDIN_ACCOUNTS` as an environment variable in the dashboard (placeholder values are
  fine — see Setup) — never real credentials in the repo.
- Log in once against the deployed instance via the UI's "1. Session" section (point its
  `API_URL` at the deployed API first) so it has a working `session_state.json`.

**UI** — deploy `streamlit_app.py` on [Streamlit Community Cloud](https://streamlit.io/cloud)
for free. In its advanced settings, pick Python 3.12. Set `API_URL` in its app secrets to
your deployed API's public URL.

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

### `POST /profile`

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
matching fallback form) exists for exactly this case: it accepts a session cookie pair
obtained by logging into the dummy account once in an ordinary browser — not scripted, not
automated by this codebase — and adopts it if LinkedIn confirms it's valid. Either path
(automated login or manual cookie) produces the same thing: a `li_at`/`JSESSIONID` pair that
every subsequent request, including all the data-fetching calls above, uses over plain HTTP.

### A dead end worth noting: `profilePositionGroups`

Before finding `profilePositions`, this project also found (and initially shipped)
`profilePositionGroups` — a *company-level* summary endpoint with the same request shape,
returning company name and date range but no job title. It's not used in the final version;
`profilePositions` (singular) turned out to be the actual per-role endpoint with titles,
descriptions, and locations. Left here because it's a good example of the false-positive risk
in this kind of endpoint discovery — a route being real and returning plausible-looking data
doesn't mean it's the *right* route for the field you're after.

## Known Limitations

- Scraping/querying LinkedIn's internal API goes against their Terms of Service — that's
  inherent to this approach, not a bug.
- `location` is only a country code (e.g. `"IN"`) — the endpoint used here doesn't expose a
  readable city/region string, and resolving the `geoUrn` it does return to one would need a
  second endpoint that wasn't identified.
- **LinkedIn's automated login is blocked in practice, repeatedly and consistently.** The
  credentials-based HTTP login is fully implemented and is what's attempted first, but it was
  checkpointed on every live attempt during development, across multiple different accounts
  (including a personal one, tested once at the account owner's explicit request). Until/
  unless that changes, most accounts will need the manual cookie fallback (`/session/
  bootstrap` or the UI's fallback form) before the API can serve requests with them.
- **Sessions on the accounts used during development died unusually fast**, sometimes within
  minutes — including once logging a manually-copied cookie's *own browser tab* out
  simultaneously. Root-caused during development: a plain Python HTTP client's TLS handshake
  doesn't match a real browser's, and LinkedIn was revoking the session (not just blocking
  "our" copy of it — `li_at` *is* the session) the instant it saw that mismatch. Switched to
  `curl_cffi` to fix this (see Approach) partway through development, so accounts flagged
  *before* that switch remain flagged — it doesn't retroactively un-flag them.
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
