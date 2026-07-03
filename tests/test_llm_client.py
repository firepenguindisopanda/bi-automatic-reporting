"""Unit tests for LLMClient.with_model().

Verifies that ``with_model`` produces a cloned instance with a different
model while leaving the original unchanged.
"""

from app.llm.client import LLMClient


class TestWithModel:
    """5.1 — LLMClient.with_model() isolation."""

    def test_with_model_creates_new_instance(self) -> None:
        original = LLMClient(model="meta/llama-3.1-8b-instruct")
        cloned = original.with_model("mistralai/mixtral-8x7b-instruct")

        assert cloned is not original
        assert cloned is not None

    def test_with_model_different_model(self) -> None:
        original = LLMClient(model="meta/llama-3.1-8b-instruct")
        cloned = original.with_model("mistralai/mixtral-8x7b-instruct")

        assert cloned._model == "mistralai/mixtral-8x7b-instruct"

    def test_original_unchanged_after_with_model(self) -> None:
        original = LLMClient(model="meta/llama-3.1-8b-instruct")
        _cloned = original.with_model("mistralai/mixtral-8x7b-instruct")

        assert original._model == "meta/llama-3.1-8b-instruct"

    def test_with_model_empty_string_uses_default(self) -> None:
        """Passing empty string should use the default model."""
        from app.config import settings

        client = LLMClient(model="")
        # settings.nvidia_model is the default
        assert client._model == settings.nvidia_model

    def test_multiple_clones_independent(self) -> None:
        original = LLMClient(model="model-a")
        clone1 = original.with_model("model-b")
        clone2 = original.with_model("model-c")

        assert clone1._model == "model-b"
        assert clone2._model == "model-c"
        assert original._model == "model-a"
