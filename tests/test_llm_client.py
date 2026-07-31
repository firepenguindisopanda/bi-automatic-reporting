"""Unit tests for LLMClient.with_model().

Verifies that ``with_model`` produces a cloned instance with a different
model while leaving the original unchanged.
"""

from app.llm.client import LLMClient


class TestWithModel:
    """5.1 - LLMClient.with_model() isolation."""

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


class TestChatCaching:
    """The underlying ChatNVIDIA (and its httpx pool) must be shared.

    Previously every with_model() call built a fresh client on every agent
    invocation, churning connection pools for the whole pipeline.
    """

    def setup_method(self) -> None:
        from app.llm.client import _CHAT_CACHE

        _CHAT_CACHE.clear()

    def test_same_config_reuses_upstream_client(self) -> None:
        a = LLMClient(model="meta/llama-3.1-8b-instruct")
        b = LLMClient(model="meta/llama-3.1-8b-instruct")
        assert a is not b
        assert a._llm is b._llm

    def test_with_model_reuses_upstream_client(self) -> None:
        base = LLMClient(model="meta/llama-3.1-8b-instruct")
        again = base.with_model("meta/llama-3.1-8b-instruct")
        assert again._llm is base._llm

    def test_different_model_gets_its_own_client(self) -> None:
        a = LLMClient(model="model-a")
        b = a.with_model("model-b")
        assert a._llm is not b._llm

    def test_different_temperature_gets_its_own_client(self) -> None:
        a = LLMClient(model="model-a", temperature=0.0)
        b = LLMClient(model="model-a", temperature=0.7)
        assert a._llm is not b._llm

    def test_cache_holds_one_entry_per_config(self) -> None:
        from app.llm.client import _CHAT_CACHE

        for _ in range(10):
            LLMClient(model="model-a").with_model("model-a")
        assert len(_CHAT_CACHE) == 1


class TestUsageExtraction:
    def test_reads_usage_metadata(self) -> None:
        from app.llm.client import _usage

        class Msg:
            usage_metadata = {"input_tokens": 120, "output_tokens": 40}

        assert _usage(Msg()) == (120, 40)

    def test_falls_back_to_response_metadata(self) -> None:
        from app.llm.client import _usage

        class Msg:
            response_metadata = {"token_usage": {"prompt_tokens": 7, "completion_tokens": 3}}

        assert _usage(Msg()) == (7, 3)

    def test_missing_usage_is_zero(self) -> None:
        from app.llm.client import _usage

        assert _usage(object()) == (0, 0)
