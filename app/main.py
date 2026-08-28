import asyncio
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import KEEP_ALIVE_INTERVAL_SECONDS, LINKEDIN_ACCOUNTS, RATE_LIMIT_PER_MINUTE
from app.schema import BootstrapRequest, LoginRequest, ProfileRequest, ProfileResponse
from app.scraper import (
    ScrapeError,
    bootstrap_session,
    has_saved_session,
    keep_session_alive,
    login_with_credentials,
    scrape_profile,
)

logger = logging.getLogger("linkedin_profile_api")


async def _keep_alive_loop():
    while True:
        await asyncio.sleep(KEEP_ALIVE_INTERVAL_SECONDS)
        try:
            await keep_session_alive()
        except Exception:
            logger.exception("Keep-alive session refresh failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_keep_alive_loop())
    yield
    task.cancel()


app = FastAPI(
    title="LinkedIn Profile API",
    description="Give it a LinkedIn profile URL, get back structured JSON.",
    version="1.0.0",
    lifespan=lifespan,
)

# simple in-memory rate limiter, good enough for a single-instance deployment
_request_log = defaultdict(list)


def _rate_limited(client_ip: str) -> bool:
    now = time.time()
    window_start = now - 60
    _request_log[client_ip] = [t for t in _request_log[client_ip] if t > window_start]
    if len(_request_log[client_ip]) >= RATE_LIMIT_PER_MINUTE:
        return True
    _request_log[client_ip].append(now)
    return False


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/session/status")
async def session_status():
    return {"configured": has_saved_session()}


@app.post("/session/bootstrap")
async def session_bootstrap(body: BootstrapRequest):
    ok = await bootstrap_session(body.cookie_header)
    if not ok:
        raise HTTPException(
            status_code=401,
            detail="Those cookies don't look valid — double-check them and try again.",
        )
    return {"status": "ok"}


@app.post("/session/login")
async def session_login(body: LoginRequest):
    try:
        await login_with_credentials(body.username, body.password)
    except ScrapeError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return {"status": "ok"}


@app.post("/session/auto-login")
async def session_auto_login():
    """Try logging in with whichever account is first in .env's
    LINKEDIN_ACCOUNTS, instead of requiring it to be re-typed into the UI."""
    if not LINKEDIN_ACCOUNTS:
        raise HTTPException(status_code=400, detail="LINKEDIN_ACCOUNTS is empty in .env.")
    username, password = LINKEDIN_ACCOUNTS[0]
    try:
        await login_with_credentials(username, password)
    except ScrapeError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return {"status": "ok", "username": username}


@app.post("/profile", response_model=ProfileResponse)
async def get_profile(body: ProfileRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if _rate_limited(client_ip):
        raise HTTPException(status_code=429, detail="Too many requests, slow down.")

    try:
        return await scrape_profile(body.url)
    except ScrapeError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except Exception:
        # something unexpected talking to LinkedIn's API — log it, but don't leak internals to the client
        logger.exception("Unhandled error scraping %s", body.url)
        raise HTTPException(status_code=500, detail="Failed to scrape the profile.")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
