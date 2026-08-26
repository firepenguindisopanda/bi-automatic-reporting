"""Tests for BI API routes."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestHealth:
    def test_health_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestBiSubmit:
    def test_submit_rejects_short_url(self, client: TestClient) -> None:
        resp = client.post(
            "/api/bi/submit",
            json={"url": "x", "email": "test@test.com"},
        )
        assert resp.status_code == 422

    def test_submit_rejects_bad_email(self, client: TestClient) -> None:
        resp = client.post(
            "/api/bi/submit",
            json={"url": "https://example.com", "email": ""},
        )
        assert resp.status_code == 422

    def test_submit_returns_job_id(self, client: TestClient) -> None:
        resp = client.post(
            "/api/bi/submit",
            json={"url": "https://example.com", "email": "test@test.com"},
        )
        # Without NVIDIA_API_KEY the LLM client will warn but the endpoint
        # should still accept the request and return a job ID
        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "processing"


class TestBiStatus:
    def test_status_404_for_missing(self, client: TestClient) -> None:
        resp = client.get("/api/bi/status/nonexistent")
        assert resp.status_code == 404

    def test_status_exists(self, client: TestClient) -> None:
        submit_resp = client.post(
            "/api/bi/submit",
            json={"url": "https://example.com", "email": "test@test.com"},
        )
        job_id = submit_resp.json()["job_id"]

        resp = client.get(f"/api/bi/status/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == job_id
        assert data["status"] in ("pending", "scraping", "analyzing", "complete", "error")


class TestBiDownload:
    def test_download_404_for_missing(self, client: TestClient) -> None:
        resp = client.get("/api/bi/download/nonexistent")
        assert resp.status_code == 404

    def test_download_400_for_incomplete(self, client: TestClient) -> None:
        submit_resp = client.post(
            "/api/bi/submit",
            json={"url": "https://example.com", "email": "test@test.com"},
        )
        job_id = submit_resp.json()["job_id"]

        resp = client.get(f"/api/bi/download/{job_id}")
        # Job likely still processing, so should be 400
        assert resp.status_code in (400, 404)


class TestQueueFull:
    """A full backlog is the queue working, so it must not read as a server fault."""

    @pytest.fixture
    def refuse(self, monkeypatch):
        import app.orchestrator.runner as job_runner

        async def full(job_id, coro):
            coro.close()
            raise job_runner.QueueFullError("The job queue is full. Try again shortly.")

        monkeypatch.setattr(job_runner, "enqueue", full)

    def test_bi_submit_returns_503_not_500(self, client: TestClient, refuse) -> None:
        resp = client.post(
            "/api/bi/submit",
            json={"url": "https://example.com", "email": "test@test.com"},
        )
        assert resp.status_code == 503
        assert resp.headers["retry-after"] == "60"
        assert "queue is full" in resp.json()["detail"]

    def test_research_submit_returns_503_not_500(self, client: TestClient, refuse) -> None:
        resp = client.post("/api/market-research/submit", json={"market_query": "Dev tools"})
        assert resp.status_code == 503
        assert resp.headers["retry-after"] == "60"
