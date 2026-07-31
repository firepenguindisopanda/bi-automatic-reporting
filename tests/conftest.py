"""Shared test fixtures and mock LLM helpers.

All tests use mocked LLM calls - never hit a real API.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock

import pytest

# Neutralise anything in .env that would make tests touch shared infrastructure.
# These must be set before app.config is first imported.
os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("JOBS_DB", ":memory:")
os.environ.setdefault("UPSTASH_REDIS_REST_URL", "")
os.environ.setdefault("UPSTASH_REDIS_REST_TOKEN", "")
os.environ.setdefault("EMAIL_PROVIDER", "console")
os.environ.setdefault("SENDGRID_API_KEY", "")

from app.llm.client import LLMClient


class MockLLM:
    """Stands in for ChatNVIDIA. Records calls and replays preset responses.

    ``responses`` is consumed one entry per call, so a test can script a
    malformed first reply followed by a valid one to exercise the retry ladder.
    Once exhausted, the last entry repeats.

    Usage in tests::

        client = LLMClient()
        client._llm = MockLLM(responses=['{"company_name": "Acme"}'])
    """

    def __init__(self, return_value: Any = None, responses: list[str] | None = None) -> None:
        self.return_value = return_value
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def _next_content(self) -> str:
        if self.responses:
            idx = min(len(self.calls) - 1, len(self.responses) - 1)
            return self.responses[idx]
        return str(self.return_value) if self.return_value is not None else ""

    async def ainvoke(self, messages: list) -> MagicMock:
        self.calls.append({"messages": messages})
        mock = MagicMock()
        mock.content = self._next_content()
        mock.usage_metadata = {"input_tokens": 100, "output_tokens": 50}
        return mock

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.return_value


@pytest.fixture
def mock_llm_client() -> LLMClient:
    """Return an LLMClient whose underlying ChatNVIDIA is replaced with a MockLLM.

    The mock returns empty content by default - override ``mock_llm.return_value``
    in individual tests to control what ``ainvoke_structured`` returns.
    """
    client = LLMClient(model="test-model")
    mock_llm = MockLLM(return_value={})
    client._llm = mock_llm
    return client


@pytest.fixture
def anyio_backend() -> str:
    """pytest-asyncio backend selection."""
    return "asyncio"


@pytest.fixture(autouse=True)
def no_real_jobs(request, monkeypatch):
    """Submitting must not actually run a pipeline.

    Route tests only care that a job is accepted and recorded. Without this,
    every submit scrapes a live site and then retries LLM calls with backoff
    against an unset API key, which is both slow and a network dependency.

    Tests that exercise the queue itself opt out with @pytest.mark.real_jobs.
    """
    if request.node.get_closest_marker("real_jobs"):
        return
    if request.module.__name__ in ("test_job_queue", "test_pipeline_integration"):
        return

    import app.orchestrator.runner as job_runner

    async def swallow(job_id: str, coro) -> int:
        coro.close()
        return 0

    monkeypatch.setattr(job_runner, "enqueue", swallow)
