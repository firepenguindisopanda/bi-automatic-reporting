"""Tests for the outbound URL policy (SSRF guard)."""

import ipaddress
import socket

import httpx
import pytest

from app.config import settings
from app.scraper.engine import ScraperEngine
from app.scraper.guard import BlockedURLError, validate_url


@pytest.fixture(autouse=True)
def _guard_enabled(monkeypatch):
    monkeypatch.setattr(settings, "scraper_block_private_networks", True)
    monkeypatch.setattr(settings, "scraper_max_redirects", 5)


@pytest.fixture
def no_dns(monkeypatch):
    """Resolve every hostname to a fixed public address.

    Keeps the tests offline and makes hostname-based cases deterministic.
    """

    def fake(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


class TestSchemes:
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "gopher://example.com/",
            "ftp://example.com/x",
            "data:text/html,hi",
            "//example.com/x",
        ],
    )
    def test_rejects_non_http_schemes(self, url: str) -> None:
        with pytest.raises(BlockedURLError):
            validate_url(url)

    def test_allows_http_and_https(self, no_dns) -> None:
        assert validate_url("http://example.com/")
        assert validate_url("https://example.com/")


class TestLiteralAddresses:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8080/",  # the Pi's own services
            "http://127.1/",
            "http://0.0.0.0/",
            "http://10.0.0.5/",
            "http://192.168.1.1/",
            "http://172.16.0.1/",
            "http://169.254.169.254/latest/meta-data/",  # cloud metadata
            "http://100.100.100.100/",  # Tailscale CGNAT range
            "http://[::1]/",
            "http://[fe80::1]/",
            "http://[fc00::1]/",
            "http://[::ffff:127.0.0.1]/",  # IPv4-mapped loopback
            "http://192.0.0.1/",
            "http://198.18.0.1/",
            "http://224.0.0.1/",
        ],
    )
    def test_rejects_internal_addresses(self, url: str) -> None:
        with pytest.raises(BlockedURLError):
            validate_url(url)

    def test_allows_public_address(self) -> None:
        assert validate_url("http://93.184.216.34/")


class TestHostnames:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8000/",
            "http://jenkins.local/",
            "http://foo.internal/",
            "http://box.home.arpa/",
            "http://metadata.google.internal/",
            "http://LOCALHOST/",  # case
            "http://localhost./",  # trailing dot
        ],
    )
    def test_rejects_internal_names(self, url: str) -> None:
        with pytest.raises(BlockedURLError):
            validate_url(url)

    def test_rejects_name_resolving_to_private_address(self, monkeypatch) -> None:
        def fake(host, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", fake)
        with pytest.raises(BlockedURLError, match="private"):
            validate_url("http://sneaky.example.com/")

    def test_rejects_when_any_resolved_address_is_private(self, monkeypatch) -> None:
        """A name resolving to both public and private addresses is refused."""

        def fake(host, *args, **kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
            ]

        monkeypatch.setattr(socket, "getaddrinfo", fake)
        with pytest.raises(BlockedURLError):
            validate_url("http://dual.example.com/")

    def test_rejects_unresolvable_host(self, monkeypatch) -> None:
        def boom(host, *args, **kwargs):
            raise socket.gaierror("nope")

        monkeypatch.setattr(socket, "getaddrinfo", boom)
        with pytest.raises(BlockedURLError, match="resolve"):
            validate_url("http://nx.example.com/")

    def test_rejects_missing_host(self) -> None:
        with pytest.raises(BlockedURLError):
            validate_url("http:///path")


class TestKillSwitch:
    def test_guard_can_be_disabled(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "scraper_block_private_networks", False)
        assert validate_url("http://127.0.0.1:8080/")


class TestRedirectHops:
    """A public URL that redirects inward must not slip through."""

    async def test_blocks_redirect_to_loopback(self, no_dns) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "example.com":
                return httpx.Response(302, headers={"location": "http://127.0.0.1:8080/admin"})
            raise AssertionError(f"guard let the request through to {request.url}")

        engine = ScraperEngine()
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
            with pytest.raises(BlockedURLError):
                await engine._fetch(client, "http://example.com/")

    async def test_blocks_redirect_chain_ending_internal(self, no_dns) -> None:
        hops = {
            "example.com": "http://second.example.com/",
            "second.example.com": "http://169.254.169.254/latest/meta-data/",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            if host in hops:
                return httpx.Response(302, headers={"location": hops[host]})
            raise AssertionError(f"guard let the request through to {request.url}")

        engine = ScraperEngine()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as client:
            with pytest.raises(BlockedURLError):
                await engine._fetch(client, "http://example.com/")

    async def test_follows_allowed_redirect(self, no_dns) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/":
                return httpx.Response(302, headers={"location": "http://example.com/final"})
            return httpx.Response(200, text="<html><title>Final</title></html>")

        engine = ScraperEngine()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as client:
            resp = await engine._fetch(client, "http://example.com/")
        assert resp.status_code == 200
        assert "Final" in resp.text

    async def test_caps_redirect_depth(self, no_dns, monkeypatch) -> None:
        monkeypatch.setattr(settings, "scraper_max_redirects", 2)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "http://example.com/next"})

        engine = ScraperEngine()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as client:
            with pytest.raises(BlockedURLError, match="redirect"):
                await engine._fetch(client, "http://example.com/")


class TestScrapeSurfacesBlock:
    async def test_scrape_returns_error_instead_of_raising(self) -> None:
        result = await ScraperEngine().scrape("http://127.0.0.1:8080/")
        assert result.error is not None
        assert "Refusing to fetch" in result.error
        assert result.text_content == ""


class TestAddressClassification:
    """Guards against a stdlib change silently widening what we allow."""

    @pytest.mark.parametrize(
        "addr",
        ["127.0.0.1", "10.0.0.1", "192.168.0.1", "169.254.169.254", "100.64.0.1", "::1"],
    )
    def test_known_internal_addresses_are_flagged(self, addr: str) -> None:
        from app.scraper.guard import _is_blocked_address

        assert _is_blocked_address(ipaddress.ip_address(addr)) is not None

    @pytest.mark.parametrize("addr", ["93.184.216.34", "1.1.1.1", "2606:4700::1111"])
    def test_public_addresses_are_allowed(self, addr: str) -> None:
        from app.scraper.guard import _is_blocked_address

        assert _is_blocked_address(ipaddress.ip_address(addr)) is None
