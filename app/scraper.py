import asyncio
import json
import os
import re

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

from app.config import LINKEDIN_ACCOUNTS, REQUEST_TIMEOUT_MS, SESSION_STATE_PATH
from app.schema import EducationItem, ExperienceItem, ProfileResponse


class ScrapeError(Exception):
    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


LOGIN_URL = "https://www.linkedin.com/uas/login"
LOGIN_SUBMIT_URL = "https://www.linkedin.com/uas/login-submit"
PROFILE_API_URL = "https://www.linkedin.com/voyager/api/identity/dash/profiles"
SECTION_API_URL = "https://www.linkedin.com/voyager/api/identity/dash/{resource}"

MONTH_NAMES = ("", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# curl_cffi matches this browser's actual TLS/JA3 handshake, not just its
# User-Agent header — LinkedIn was observed revoking sessions (even the
# original browser's own session!) the instant a request came in with a
# generic Python HTTP client's TLS fingerprint, regardless of how correct
# the headers/cookies were. This is what actually fixes that.
IMPERSONATE = "chrome"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

CSRF_PARAM_RE = re.compile(r'name="loginCsrfParam"\s+value="([^"]*)"')
VANITY_NAME_RE = re.compile(r"linkedin\.com/in/([a-zA-Z0-9\-_%]+)", re.IGNORECASE)

_TIMEOUT = REQUEST_TIMEOUT_MS / 1000

_current_account_index = 0
_cookies: "dict[str, str] | None" = None
_lock = asyncio.Lock()


def _session_path_for(index: int) -> str:
    # the first account keeps the plain filename so an existing saved session
    # from before multi-account support was added still gets picked up
    if index == 0:
        return SESSION_STATE_PATH
    root, ext = os.path.splitext(SESSION_STATE_PATH)
    return f"{root}_{index}{ext}"


def _save_cookies(cookies: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cookies, f)


def _load_cookies(path: str) -> "dict | None":
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _cookie_header(cookies: dict) -> str:
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


def _api_headers(cookies: dict) -> dict:
    jsessionid = (cookies.get("JSESSIONID") or "").strip('"')
    return {
        "csrf-token": jsessionid,
        "x-restli-protocol-version": "2.0.0",
        "accept": "application/json",
        "referer": "https://www.linkedin.com/",
        "user-agent": USER_AGENT,
        "cookie": _cookie_header(cookies),
    }


async def _validate_cookies(cookies: dict) -> bool:
    """Cheap check that a saved/just-obtained cookie jar is still authenticated."""
    async with AsyncSession() as session:
        try:
            resp = await session.get(
                PROFILE_API_URL,
                params={"q": "memberIdentity", "memberIdentity": "linkedin"},
                headers=_api_headers(cookies),
                impersonate=IMPERSONATE,
                timeout=_TIMEOUT,
            )
        except RequestException:
            return False
        return resp.status_code == 200


async def _login_http(username: str, password: str) -> dict:
    """Log in with a plain HTTP POST — no browser involved. This is the
    riskiest, most bot-detectable step; see README for the observed risk."""
    async with AsyncSession(headers={"user-agent": USER_AGENT}) as session:
        try:
            resp = await session.get(LOGIN_URL, impersonate=IMPERSONATE, timeout=_TIMEOUT)
            # LinkedIn's login page is JS-rendered now — the classic hidden
            # "loginCsrfParam" form field isn't in the static HTML anymore.
            # The JSESSIONID set on this same request is what protects the
            # submit endpoint instead (same pattern the profile API uses).
            csrf_match = CSRF_PARAM_RE.search(resp.text)
            csrf_param = csrf_match.group(1) if csrf_match else ""
            jsessionid = (session.cookies.get("JSESSIONID") or "").strip('"')

            resp = await session.post(
                LOGIN_SUBMIT_URL,
                data={
                    "session_key": username,
                    "session_password": password,
                    "loginCsrfParam": csrf_param,
                },
                headers={"referer": LOGIN_URL, "csrf-token": jsessionid},
                impersonate=IMPERSONATE,
                timeout=_TIMEOUT,
            )
        except RequestException as e:
            raise ScrapeError(f"Connection to LinkedIn failed during login: {e}", 502)

        cookies = dict(session.cookies)
        if not cookies.get("li_at"):
            if "checkpoint" in str(resp.url):
                raise ScrapeError(
                    "LinkedIn threw up a security checkpoint (2FA / CAPTCHA / bot check) "
                    "during login. This account needs to be cleared manually before the "
                    "automated login can work.",
                    401,
                )
            raise ScrapeError("Login didn't produce a valid session — credentials may be wrong.", 401)

        return cookies


async def _get_cookies() -> dict:
    global _cookies, _current_account_index

    async with _lock:
        if _cookies is not None:
            return _cookies

        last_error = None
        n = len(LINKEDIN_ACCOUNTS)
        for offset in range(n):
            index = (_current_account_index + offset) % n
            username, password = LINKEDIN_ACCOUNTS[index]
            path = _session_path_for(index)

            cookies = _load_cookies(path)
            if cookies is not None and await _validate_cookies(cookies):
                _cookies = cookies
                _current_account_index = index
                return cookies

            try:
                cookies = await _login_http(username, password)
            except ScrapeError as e:
                last_error = e
                continue

            _save_cookies(cookies, path)
            _cookies = cookies
            _current_account_index = index
            return cookies

        raise last_error


async def _drop_cookies() -> None:
    global _cookies
    async with _lock:
        _cookies = None
        path = _session_path_for(_current_account_index)
        if os.path.exists(path):
            os.remove(path)


async def keep_session_alive() -> None:
    """Re-validate the current session and refresh the saved cookie file so it
    doesn't go idle. Safe to call on a timer from outside."""
    cookies = await _get_cookies()
    if not await _validate_cookies(cookies):
        await _drop_cookies()
        return
    _save_cookies(cookies, _session_path_for(_current_account_index))


def _parse_cookie_header(cookie_header: str) -> dict:
    """Parse a raw `Cookie: a=1; b=2; ...` header value (copied whole from a
    browser's Network tab) into a plain {name: value} dict with everything
    intact."""
    cookies = {}
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        cookies[name.strip()] = value.strip()
    return cookies


async def bootstrap_session(cookie_header: str) -> bool:
    """Accept a manually-obtained full Cookie header (see README — LinkedIn's
    login endpoint blocks non-browser login attempts) and adopt it as the
    active session if it actually works. Using the *whole* cookie set (not
    just li_at/JSESSIONID) matters: replaying only a couple of cookies without
    the rest of the browser's original set looks like a different device
    reusing a stolen session token to LinkedIn's fraud detection, and gets
    rejected even when li_at itself is genuinely fresh and valid.
    Returns whether it was accepted."""
    global _cookies

    cookies = _parse_cookie_header(cookie_header)
    if not cookies.get("li_at") or not cookies.get("JSESSIONID"):
        return False

    if not await _validate_cookies(cookies):
        return False

    async with _lock:
        _save_cookies(cookies, _session_path_for(_current_account_index))
        _cookies = cookies
    return True


async def login_with_credentials(username: str, password: str) -> None:
    """On-demand version of the same pure-HTTP login _get_cookies() falls back
    to automatically — lets the UI trigger a login with a specific account
    right now instead of waiting for the next /profile request to need one.
    Raises ScrapeError (with a checkpoint/bad-credentials message) on failure."""
    global _cookies

    cookies = await _login_http(username.strip(), password)

    async with _lock:
        _save_cookies(cookies, _session_path_for(_current_account_index))
        _cookies = cookies


def has_saved_session() -> bool:
    """Cheap, non-network check of whether a session file exists for the
    currently active account — doesn't confirm it's still valid."""
    return os.path.exists(_session_path_for(_current_account_index))


def _extract_vanity_name(url: str) -> str:
    match = VANITY_NAME_RE.search(url)
    if not match:
        raise ScrapeError("Couldn't parse a LinkedIn vanity name out of that URL.", 400)
    return match.group(1)


def _build_profile_image_url(element: dict) -> str | None:
    vector = ((element.get("profilePicture") or {}).get("displayImage") or {}).get("vectorImage")
    if not vector or not vector.get("artifacts"):
        return None
    best = max(vector["artifacts"], key=lambda a: a.get("width", 0))
    return vector["rootUrl"] + best["fileIdentifyingUrlPathSegment"]


def _parse_basic_profile(element: dict) -> dict:
    first = (element.get("multiLocaleFirstName") or {}).get("en_US") or element.get("firstName")
    last = (element.get("multiLocaleLastName") or {}).get("en_US") or element.get("lastName")
    name = " ".join(p for p in (first, last) if p) or None

    summary_map = element.get("multiLocaleSummary") or {}
    about = next(iter(summary_map.values()), None)

    # LinkedIn's dash/profiles response only exposes a country code here
    # (e.g. "IN"), not a display string like "Bengaluru, Karnataka, India" —
    # resolving the geo URN to a readable place name needs a separate
    # endpoint this project wasn't able to identify (see README)
    location = (element.get("location") or {}).get("countryCode")

    return {
        "name": name,
        "headline": element.get("headline"),
        "location": location,
        "about": about,
        "profile_image_url": _build_profile_image_url(element),
    }


def _format_date(date: "dict | None") -> "str | None":
    if not date or not date.get("year"):
        return None
    month = date.get("month")
    if month:
        return f"{MONTH_NAMES[month]} {date['year']}"
    return str(date["year"])


def _format_date_range(date_range: "dict | None") -> "str | None":
    if not date_range:
        return None
    start = _format_date(date_range.get("start"))
    end = _format_date(date_range.get("end"))
    if start and end:
        return f"{start} - {end}"
    if start:
        return f"{start} - Present"
    return end


async def _fetch_section(cookies: dict, resource: str, profile_urn: str) -> list:
    """Best-effort: a broken/rate-limited section call degrades to an empty
    list rather than failing the whole /profile response."""
    async with AsyncSession() as session:
        try:
            resp = await session.get(
                SECTION_API_URL.format(resource=resource),
                params={"q": "viewee", "profileUrn": profile_urn},
                headers=_api_headers(cookies),
                impersonate=IMPERSONATE,
                timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                return []
            return resp.json().get("elements") or []
        except (RequestException, ValueError):
            return []


def _parse_education(elements: list) -> list[EducationItem]:
    items = []
    for el in elements:
        degree = ", ".join(p for p in (el.get("degreeName"), el.get("fieldOfStudy")) if p) or None
        items.append(
            EducationItem(
                school=el.get("schoolName"),
                degree=degree,
                duration=_format_date_range(el.get("dateRange")),
            )
        )
    return items


def _parse_names(elements: list) -> list[str]:
    return [el["name"] for el in elements if el.get("name")]


def _text_field(el: dict, plain_key: str, multi_key: str) -> "str | None":
    """LinkedIn's Voyager API duplicates most text fields as both a plain
    string and a `multiLocale<Field>` dict keyed by locale — try both, since
    which one is populated seems to vary by field/resource."""
    val = el.get(plain_key)
    if isinstance(val, str) and val:
        return val
    multi = el.get(multi_key) or {}
    if isinstance(multi, dict) and multi:
        return next(iter(multi.values()), None)
    return None


def _parse_experience(elements: list) -> list[ExperienceItem]:
    items = []
    for el in elements:
        items.append(
            ExperienceItem(
                title=_text_field(el, "title", "multiLocaleTitle"),
                company=_text_field(el, "companyName", "multiLocaleCompanyName"),
                duration=_format_date_range(el.get("dateRange")),
                location=_text_field(el, "geoLocationName", "multiLocaleGeoLocationName"),
                description=_text_field(el, "description", "multiLocaleDescription"),
            )
        )
    return items


PROFICIENCY_LABELS = {
    "ELEMENTARY": "Elementary proficiency",
    "LIMITED_WORKING": "Limited working proficiency",
    "PROFESSIONAL_WORKING": "Professional working proficiency",
    "FULL_PROFESSIONAL": "Full professional proficiency",
    "NATIVE_OR_BILINGUAL": "Native or bilingual proficiency",
}


def _parse_languages(elements: list) -> list[str]:
    result = []
    for el in elements:
        name = el.get("name")
        if not name:
            continue
        proficiency = PROFICIENCY_LABELS.get(el.get("proficiency"), el.get("proficiency"))
        result.append(f"{name} ({proficiency})" if proficiency else name)
    return result


async def scrape_profile(url: str) -> ProfileResponse:
    vanity_name = _extract_vanity_name(url)
    cookies = await _get_cookies()

    async with AsyncSession() as session:
        try:
            resp = await session.get(
                PROFILE_API_URL,
                params={"q": "memberIdentity", "memberIdentity": vanity_name},
                headers=_api_headers(cookies),
                impersonate=IMPERSONATE,
                timeout=_TIMEOUT,
            )
        except RequestException as e:
            raise ScrapeError(f"Connection to LinkedIn failed: {e}", 502)

    if resp.status_code in (401, 403):
        await _drop_cookies()
        raise ScrapeError(
            "Session expired, hit a security checkpoint, or got rate-limited. "
            "Try again shortly — the next request will log in again automatically.",
            401,
        )
    if resp.status_code != 200:
        raise ScrapeError(f"LinkedIn returned an unexpected status: {resp.status_code}", 502)

    try:
        data = resp.json()
    except ValueError:
        raise ScrapeError("LinkedIn returned a response that wasn't valid JSON.", 502)

    elements = data.get("elements") or []
    if not elements:
        raise ScrapeError("Profile is private, restricted, or doesn't exist.", 404)

    element = elements[0]
    basic = _parse_basic_profile(element)
    profile_urn = element.get("entityUrn")

    experience, education, certifications, skills, languages = [], [], [], [], []
    if profile_urn:
        (
            position_elements,
            education_elements,
            certification_elements,
            skill_elements,
            language_elements,
        ) = await asyncio.gather(
            _fetch_section(cookies, "profilePositions", profile_urn),
            _fetch_section(cookies, "profileEducations", profile_urn),
            _fetch_section(cookies, "profileCertifications", profile_urn),
            _fetch_section(cookies, "profileSkills", profile_urn),
            _fetch_section(cookies, "profileLanguages", profile_urn),
        )
        experience = _parse_experience(position_elements)
        education = _parse_education(education_elements)
        certifications = _parse_names(certification_elements)
        skills = _parse_names(skill_elements)
        languages = _parse_languages(language_elements)

    return ProfileResponse(
        **basic,
        experience=experience,
        education=education,
        skills=skills,
        certifications=certifications,
        languages=languages,
    )
