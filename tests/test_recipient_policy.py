"""The submitter names the recipient, so sending is limited by policy."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.email.policy import parse_allowlist
from app.main import app


def _unique_email(domain: str = "example.com") -> str:
    return f"{uuid.uuid4().hex[:10]}@{domain}"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestAllowlist:
    def test_empty_allows_anyone(self) -> None:
        allow = parse_allowlist("")
        assert allow.open
        assert allow.permits("anyone@anywhere.example")

    def test_exact_address(self) -> None:
        allow = parse_allowlist("me@example.com")
        assert allow.permits("ME@example.com")
        assert not allow.permits("you@example.com")

    @pytest.mark.parametrize("entry", ["@example.org", "example.org"])
    def test_domain_entries(self, entry: str) -> None:
        allow = parse_allowlist(f" {entry} , other@example.net")
        assert allow.permits("anyone@example.org")
        assert allow.permits("other@example.net")
        assert not allow.permits("anyone@example.net")

    def test_a_domain_does_not_match_its_lookalike(self) -> None:
        assert not parse_allowlist("@example.org").permits("x@evil-example.org")


class TestSubmitValidation:
    @pytest.mark.parametrize("email", ["not-an-email", "a@", "@b.com", "a b@c.com", "x" * 300 + "@c.com"])
    def test_rejects_malformed_addresses(self, client: TestClient, email: str) -> None:
        resp = client.post("/api/bi/submit", json={"url": "https://example.com", "email": email})
        assert resp.status_code == 422

    def test_rejects_a_header_injection_attempt(self, client: TestClient) -> None:
        resp = client.post(
            "/api/bi/submit",
            json={"url": "https://example.com", "email": "a@example.com\nBcc: b@example.com"},
        )
        assert resp.status_code == 422


class TestSubmitPolicy:
    def test_allowlist_refuses_others(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.setattr(settings, "recipient_allowlist", "@allowed.example")
        refused = client.post(
            "/api/bi/submit", json={"url": "https://example.com", "email": _unique_email()}
        )
        assert refused.status_code == 403
        accepted = client.post(
            "/api/bi/submit",
            json={"url": "https://example.com", "email": _unique_email("allowed.example")},
        )
        assert accepted.status_code == 200

    def test_daily_cap_per_recipient(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.setattr(settings, "max_reports_per_recipient_per_day", 2)
        email = _unique_email()
        for _ in range(2):
            ok = client.post("/api/bi/submit", json={"url": "https://example.com", "email": email})
            assert ok.status_code == 200
        capped = client.post("/api/bi/submit", json={"url": "https://example.com", "email": email})
        assert capped.status_code == 429
        assert "24 hours" in capped.json()["detail"]

    def test_the_cap_ignores_case(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.setattr(settings, "max_reports_per_recipient_per_day", 1)
        local = uuid.uuid4().hex[:10]
        first = client.post(
            "/api/bi/submit", json={"url": "https://example.com", "email": f"{local}@example.com"}
        )
        assert first.status_code == 200
        second = client.post(
            "/api/bi/submit",
            json={"url": "https://example.com", "email": f"{local.upper()}@Example.com"},
        )
        assert second.status_code == 429

    def test_other_recipients_are_unaffected(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.setattr(settings, "max_reports_per_recipient_per_day", 1)
        assert client.post(
            "/api/bi/submit", json={"url": "https://example.com", "email": _unique_email()}
        ).status_code == 200
        assert client.post(
            "/api/bi/submit", json={"url": "https://example.com", "email": _unique_email()}
        ).status_code == 200


class TestJobActions:
    def test_resend_unknown_job_is_404(self, client: TestClient) -> None:
        assert client.post("/api/bi/nope/resend").status_code == 404

    def test_resend_unfinished_job_is_409(self, client: TestClient) -> None:
        job = client.post(
            "/api/bi/submit", json={"url": "https://example.com", "email": _unique_email()}
        ).json()
        resp = client.post(f"/api/bi/{job['job_id']}/resend")
        assert resp.status_code == 409

    def test_retry_unknown_job_is_404(self, client: TestClient) -> None:
        assert client.post("/api/bi/nope/retry").status_code == 404
        assert client.post("/api/market-research/nope/retry").status_code == 404

    def test_retry_of_a_queued_job_is_409(self, client: TestClient) -> None:
        job = client.post(
            "/api/bi/submit", json={"url": "https://example.com", "email": _unique_email()}
        ).json()
        resp = client.post(f"/api/bi/{job['job_id']}/retry")
        assert resp.status_code == 409
        assert "failed or interrupted" in resp.json()["detail"]

    def test_job_ids_are_full_length(self, client: TestClient) -> None:
        """The id is the only credential for a report, so it carries a UUID's entropy."""
        job = client.post(
            "/api/bi/submit", json={"url": "https://example.com", "email": _unique_email()}
        ).json()
        assert len(job["job_id"]) == 32
