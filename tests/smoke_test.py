"""E2E smoke test - requires a real NVIDIA_API_KEY in .env and the server running.

Usage:
    # Terminal 1: Start the server
    uvicorn app.main:app --port 8001

    # Terminal 2: Run this script
    python -m tests.smoke_test

Or run with pytest (skipped if no API key):
    pytest tests/smoke_test.py -v
"""

import os
import time

import httpx
import pytest

SMOKE_TEST_URL = os.getenv("SMOKE_TEST_URL", "http://example.com")
SMOKE_TEST_EMAIL = os.getenv("SMOKE_TEST_EMAIL", "smoke-test@bisystem.com")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8001")
POLL_INTERVAL = 3
MAX_WAIT = 120


@pytest.mark.skipif(
    not os.environ.get("NVIDIA_API_KEY"),
    reason="NVIDIA_API_KEY not set - skip real-LLM smoke test",
)
class TestSmokeE2E:
    def test_full_pipeline(self) -> None:
        client = httpx.Client(base_url=BASE_URL, timeout=30)

        # 1. Health check
        resp = client.get("/api/health")
        assert resp.status_code == 200, f"Health check failed: {resp.text}"
        print("  ✓ Health check passed")

        # 2. Submit
        resp = client.post(
            "/api/bi/submit",
            json={"url": SMOKE_TEST_URL, "email": SMOKE_TEST_EMAIL},
        )
        assert resp.status_code == 200, f"Submit failed: {resp.text}"
        data = resp.json()
        job_id = data["job_id"]
        assert data["status"] == "processing"
        print(f"  ✓ Submit returned job_id={job_id}")

        # 3. Poll until complete
        deadline = time.time() + MAX_WAIT
        final_status = None
        while time.time() < deadline:
            resp = client.get(f"/api/bi/status/{job_id}")
            assert resp.status_code == 200, f"Status check failed: {resp.text}"
            data = resp.json()
            final_status = data["status"]
            print(f"  Status: {final_status}")
            if final_status == "complete":
                break
            if final_status == "error":
                pytest.fail(f"Job failed: {data.get('error')}")
            time.sleep(POLL_INTERVAL)
        else:
            pytest.fail(f"Job did not complete within {MAX_WAIT}s (last status: {final_status})")

        print("  ✓ Pipeline completed")

        # 4. Download PDF
        resp = client.get(f"/api/bi/download/{job_id}?format=pdf")
        assert resp.status_code == 200, f"PDF download failed: {resp.text}"
        assert len(resp.content) > 1000, "PDF too small"
        print(f"  ✓ PDF downloaded ({len(resp.content)} bytes)")

        # 5. Download DOCX
        resp = client.get(f"/api/bi/download/{job_id}?format=docx")
        assert resp.status_code == 200, f"DOCX download failed: {resp.text}"
        assert len(resp.content) > 500, "DOCX too small"
        print(f"  ✓ DOCX downloaded ({len(resp.content)} bytes)")

        print("\n  ✅ E2E smoke test passed!")


if __name__ == "__main__":
    import sys

    pytest.main([__file__, "-v", "--tb=short", *sys.argv[1:]])
