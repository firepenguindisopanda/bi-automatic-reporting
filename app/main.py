import logging
import mimetypes
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp

from app.api.routes import _bi_pipeline, router
from app.config import settings
from app.llm.client import close_all
from app.orchestrator.runner import start_workers, stop_workers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


# Endpoints that start real work, and so are worth limiting. Everything else
# (status polling, downloads) is cheap and stays unlimited.
RATE_LIMITED_PATHS = frozenset({"/api/bi/submit", "/api/market-research/submit"})

_upstash_client: httpx.AsyncClient | None = None


def _get_upstash_client() -> httpx.AsyncClient:
    """One shared client. Opening a new one per request churned connections."""
    global _upstash_client
    if _upstash_client is None:
        _upstash_client = httpx.AsyncClient(timeout=httpx.Timeout(5.0))
    return _upstash_client


def client_key(request: Request) -> str:
    """Identify the caller for rate limiting.

    Behind a reverse proxy the peer address is always the proxy, so the
    proxy-set headers are the only way to tell callers apart. X-Real-IP is
    preferred because nginx overwrites it, whereas X-Forwarded-For is appended
    to and so its leading entries are attacker-controlled.
    """
    if settings.trust_proxy_headers:
        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip:
            return real_ip
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, max_requests: int = 10, window: int = 60) -> None:
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window
        self._upstash_url = settings.upstash_redis_rest_url.rstrip("/")
        self._upstash_token = settings.upstash_redis_rest_token
        self._use_upstash = bool(self._upstash_url and self._upstash_token)
        self._clients: dict[str, list[float]] = {}
        self._last_prune = 0.0

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method == "POST" and request.url.path in RATE_LIMITED_PATHS:
            if not await self._check(client_key(request)):
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded. Try again shortly."},
                    headers={"Retry-After": str(self.window)},
                )
        return await call_next(request)

    async def _check(self, key: str) -> bool:
        if self._use_upstash:
            return await self._check_upstash(key)
        return self._check_memory(key)

    async def _check_upstash(self, key: str) -> bool:
        redis_key = f"rl:{key}"
        headers = {"Authorization": f"Bearer {self._upstash_token}"}
        client = _get_upstash_client()
        try:
            resp = await client.post(f"{self._upstash_url}/INCR/{redis_key}", headers=headers)
            resp.raise_for_status()
            count = int(resp.json().get("result", 0))
            if count == 1:
                await client.post(
                    f"{self._upstash_url}/EXPIRE/{redis_key}/{self.window}", headers=headers
                )
            return count <= self.max_requests
        except (httpx.HTTPError, ValueError, KeyError) as e:
            # Fail open: a rate limiter outage must not take the service down.
            logger.warning("Upstash rate limit check failed, allowing request: %s", e)
            return True

    def _check_memory(self, key: str) -> bool:
        now = time.monotonic()
        self._prune(now)
        timestamps = [t for t in self._clients.get(key, []) if now - t < self.window]
        if len(timestamps) >= self.max_requests:
            self._clients[key] = timestamps
            return False
        timestamps.append(now)
        self._clients[key] = timestamps
        return True

    def _prune(self, now: float) -> None:
        """Drop idle callers so the dict cannot grow without bound."""
        if now - self._last_prune < self.window:
            return
        self._last_prune = now
        self._clients = {
            k: fresh
            for k, ts in self._clients.items()
            if (fresh := [t for t in ts if now - t < self.window])
        }


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await _bi_pipeline.init()
    # Anything still marked running belongs to a process that no longer exists.
    interrupted = await _bi_pipeline.recover_interrupted()
    if interrupted:
        logger.info("Marked %d interrupted job(s) from a previous run", interrupted)
    await start_workers()
    yield
    await stop_workers()
    await _bi_pipeline.close()
    if _upstash_client is not None:
        await _upstash_client.aclose()
    await close_all()


app = FastAPI(
    title=settings.app_title,
    version=settings.app_version,
    description=(
        "Business Intelligence System - scrape any website, extract key data, "
        "and generate comprehensive AI analysis reports delivered to your inbox."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    RateLimitMiddleware,
    max_requests=settings.rate_limit_max_requests,
    window=settings.rate_limit_window,
)

app.include_router(router)

static_dir = (Path(__file__).resolve().parent.parent / "static").resolve()


def safe_static_file(path: str) -> Path | None:
    """Resolve a request path to a file inside ``static_dir``, or None.

    The path arrives percent-decoded, so a segment of ``..%2f`` reaches here as
    ``../`` and an unguarded join escapes the directory - an unauthenticated
    read of anything the process can open, including ``/proc/self/environ`` and
    every secret in it. An absolute path escapes too, because ``Path.__truediv__``
    discards the left side when the right one is rooted.

    Resolving first and then checking containment is the whole guard. Comparing
    the unresolved strings is not: it passes a symlink inside static/ that points
    somewhere else.
    """
    candidate = (static_dir / path).resolve()
    if candidate != static_dir and static_dir not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


async def favicon() -> Response:
    f = safe_static_file("favicon.svg")
    return (
        Response(content=f.read_bytes(), media_type="image/svg+xml")
        if f is not None
        else Response(status_code=404)
    )


async def spa_catch_all(path: str) -> Response:
    """Serve a built asset, or hand the SPA its own routing back."""
    if path.startswith("api/"):
        return Response(status_code=404)
    fp = safe_static_file(path)
    if fp is not None:
        # SecurityHeadersMiddleware sends `nosniff`, so a file served without a
        # Content-Type is refused by the browser rather than guessed at.
        guessed, _ = mimetypes.guess_type(fp.name)
        return Response(content=fp.read_bytes(), media_type=guessed or "application/octet-stream")
    index = static_dir / "index.html"
    if index.is_file():
        return Response(content=index.read_bytes(), media_type="text/html")
    return Response(status_code=404)


# Registered only when a frontend build is present - a dev checkout has none.
# The handlers themselves stay at module level so they are testable either way.
if static_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")
    app.get("/favicon.svg")(favicon)
    app.get("/{path:path}")(spa_catch_all)

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
