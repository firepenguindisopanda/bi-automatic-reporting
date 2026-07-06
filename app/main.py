import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.staticfiles import StaticFiles

from app.api.routes import _bi_pipeline, router
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, max_requests: int = 10, window: int = 60) -> None:
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window
        self._upstash_url = settings.upstash_redis_rest_url.rstrip("/")
        self._upstash_token = settings.upstash_redis_rest_token
        self._use_upstash = bool(self._upstash_url and self._upstash_token)
        self._clients: dict[str, list[float]] = {}

    async def dispatch(self, request: Request, call_next):
        if request.method == "POST" and request.url.path == "/api/bi/submit":
            client_ip = request.client.host if request.client else "unknown"
            allowed = await self._check(client_ip)
            if not allowed:
                return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
        return await call_next(request)

    async def _check(self, client_ip: str) -> bool:
        if self._use_upstash:
            return await self._check_upstash(client_ip)
        return self._check_memory(client_ip)

    async def _check_upstash(self, client_ip: str) -> bool:
        key = f"rl:{client_ip}"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._upstash_url}/INCR/{key}",
                headers={"Authorization": f"Bearer {self._upstash_token}"},
            )
            data = resp.json()
            count = int(data.get("result", 0))
            if count == 1:
                await client.post(
                    f"{self._upstash_url}/EXPIRE/{key}/{self.window}",
                    headers={"Authorization": f"Bearer {self._upstash_token}"},
                )
            return count <= self.max_requests

    def _check_memory(self, client_ip: str) -> bool:
        now = __import__("time").time()
        timestamps = self._clients.get(client_ip, [])
        timestamps = [t for t in timestamps if now - t < self.window]
        if len(timestamps) >= self.max_requests:
            return False
        timestamps.append(now)
        self._clients[client_ip] = timestamps
        return True


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await _bi_pipeline.init()
    yield


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
    allow_origins=["http://localhost:5173", "http://localhost:4173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware, max_requests=10, window=60)

app.include_router(router)

static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")

    @app.get("/favicon.svg")
    async def favicon() -> Response:
        f = static_dir / "favicon.svg"
        return Response(content=f.read_bytes(), media_type="image/svg+xml") if f.exists() else Response(status_code=404)

    @app.get("/{path:path}")
    async def spa_catch_all(path: str) -> Response:
        if path.startswith("api/"):
            return Response(status_code=404)
        fp = static_dir / path
        if fp.is_file():
            content = fp.read_bytes()
            return Response(content=content)
        index = static_dir / "index.html"
        if index.exists():
            return Response(content=index.read_bytes(), media_type="text/html")
        return Response(status_code=404)

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
