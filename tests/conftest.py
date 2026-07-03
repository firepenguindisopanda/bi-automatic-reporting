"""Shared test fixtures and mock LLM helpers.

All tests use mocked LLM calls - never hit a real API.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.llm.client import LLMClient


class MockLLM:
    """A callable that records calls and returns preset structured data.

    Usage in tests::

        mock_llm = MockLLM(return_value={"score": 0.85, "feedback": "Good"})
        client = LLMClient()
        client._llm = mock_llm   # replace the real ChatNVIDIA
    """

    def __init__(self, return_value: Any = None) -> None:
        self.return_value = return_value
        self.calls: list[dict[str, Any]] = []

    def invoke(self, messages: list) -> MagicMock:
        self.calls.append({"messages": messages})
        mock = MagicMock()
        mock.content = str(self.return_value) if self.return_value is not None else ""
        return mock

    def with_structured_output(self, output_model: type) -> MockLLM:
        """Return self so chaining works."""
        return self

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.return_value


@pytest.fixture
def mock_llm_client() -> LLMClient:
    """Return an LLMClient whose underlying ChatNVIDIA is replaced with a MockLLM.

    The mock returns empty content by default - override ``mock_llm.return_value``
    in individual tests to control what ``invoke_structured`` returns.
    """
    client = LLMClient(model="test-model")
    mock_llm = MockLLM(return_value={})
    client._llm = mock_llm
    return client


@pytest.fixture
def anyio_backend() -> str:
    """pytest-asyncio backend selection."""
    return "asyncio"
