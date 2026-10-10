"""Tests for rate limiting and caller identification."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.config import settings
from app.main import RATE_LIMITED_PATHS, RateLimitMiddleware, client_key, is_rate_limited


def _request(headers: dict[str, str], peer: str = "127.0.0.1") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/bi/submit",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (peer, 1234),
    }
    return Request(scope)


class TestClientKey:
    def test_prefers_x_real_ip(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "trust_proxy_headers", True)
        req = _request({"x-real-ip": "203.0.113.9", "x-forwarded-for": "1.1.1.1, 2.2.2.2"})
        assert client_key(req) == "203.0.113.9"

    def test_falls_back_to_last_forwarded_entry(self, monkeypatch) -> None:
        """Leading X-Forwarded-For entries are client-supplied, so trust the last."""
        monkeypatch.setattr(settings, "trust_proxy_headers", True)
        req = _request({"x-forwarded-for": "9.9.9.9 (spoofed), 203.0.113.9"})
        assert client_key(req) == "203.0.113.9"

    def test_ignores_headers_when_untrusted(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "trust_proxy_headers", False)
        req = _request({"x-real-ip": "203.0.113.9"}, peer="198.51.100.4")
        assert client_key(req) == "198.51.100.4"

    def test_falls_back_to_peer_without_headers(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "trust_proxy_headers", True)
        assert client_key(_request({}, peer="198.51.100.4")) == "198.51.100.4"


class TestLimitedPaths:
    def test_covers_both_submit_endpoints(self) -> None:
        # Market research submit was previously unlimited.
        assert RATE_LIMITED_PATHS == {"/api/bi/submit", "/api/market-research/submit"}


@pytest.fixture
def limited_app(monkeypatch) -> FastAPI:
    """An isolated app so limiter state cannot leak into other test modules."""
    # Belt and braces: never let a populated .env point these tests at real Upstash.
    monkeypatch.setattr(settings, "upstash_redis_rest_url", "")
    monkeypatch.setattr(settings, "upstash_redis_rest_token", "")
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    test_app = FastAPI()
    test_app.add_middleware(RateLimitMiddleware, max_requests=3, window=60)

    @test_app.post("/api/bi/submit")
    async def bi_submit() -> dict[str, str]:
        return {"ok": "yes"}

    @test_app.post("/api/market-research/submit")
    async def mr_submit() -> dict[str, str]:
        return {"ok": "yes"}

    @test_app.get("/api/bi/status/x")
    async def status() -> dict[str, str]:
        return {"ok": "yes"}

    return test_app


class TestEnforcement:
    def test_blocks_after_limit(self, limited_app: FastAPI) -> None:
        client = TestClient(limited_app)
        headers = {"x-real-ip": "203.0.113.1"}
        for _ in range(3):
            assert client.post("/api/bi/submit", headers=headers).status_code == 200
        resp = client.post("/api/bi/submit", headers=headers)
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "60"

    def test_limit_is_per_caller(self, limited_app: FastAPI) -> None:
        client = TestClient(limited_app)
        for _ in range(3):
            client.post("/api/bi/submit", headers={"x-real-ip": "203.0.113.1"})
        # A different caller behind the same proxy is unaffected.
        resp = client.post("/api/bi/submit", headers={"x-real-ip": "203.0.113.2"})
        assert resp.status_code == 200

    def test_market_research_is_limited(self, limited_app: FastAPI) -> None:
        client = TestClient(limited_app)
        headers = {"x-real-ip": "203.0.113.3"}
        for _ in range(3):
            assert client.post("/api/market-research/submit", headers=headers).status_code == 200
        assert client.post("/api/market-research/submit", headers=headers).status_code == 429

    def test_reads_are_not_limited(self, limited_app: FastAPI) -> None:
        client = TestClient(limited_app)
        headers = {"x-real-ip": "203.0.113.4"}
        for _ in range(3):
            client.post("/api/bi/submit", headers=headers)
        for _ in range(10):
            assert client.get("/api/bi/status/x", headers=headers).status_code == 200


class TestMemoryStore:
    def test_prunes_idle_callers(self) -> None:
        mw = RateLimitMiddleware(app=None, max_requests=3, window=60)  # type: ignore[arg-type]
        for i in range(50):
            mw._check_memory(f"caller-{i}")
        assert len(mw._clients) == 50
        # Jump past the window: everything recorded so far is now stale.
        mw._last_prune = 0.0
        mw._prune(now=1e9)
        assert mw._clients == {}

    def test_window_expiry_allows_again(self, monkeypatch) -> None:
        import app.main as main_mod

        clock = {"t": 1000.0}
        monkeypatch.setattr(main_mod.time, "monotonic", lambda: clock["t"])

        mw = RateLimitMiddleware(app=None, max_requests=2, window=60)  # type: ignore[arg-type]
        assert mw._check_memory("a") is True
        assert mw._check_memory("a") is True
        assert mw._check_memory("a") is False

        clock["t"] += 61
        assert mw._check_memory("a") is True


class TestLimitedActions:
    """Per-job actions spend model calls or send mail, so they are limited too."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/bi/submit",
            "/api/market-research/submit",
            "/api/bi/abc123/resend",
            "/api/bi/abc123/retry",
            "/api/market-research/abc123/retry",
        ],
    )
    def test_work_starting_posts_are_limited(self, path: str) -> None:
        assert is_rate_limited("POST", path)

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/api/bi/progress/abc123"),
            ("GET", "/api/bi/download/abc123"),
            ("GET", "/api/bi/abc123/resend"),
            ("POST", "/api/bi/brief/abc123"),
            ("POST", "/api/bi/a/b/resend"),
        ],
    )
    def test_reads_and_other_posts_are_not(self, method: str, path: str) -> None:
        assert not is_rate_limited(method, path)
