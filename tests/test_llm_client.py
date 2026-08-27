"""Unit tests for LLMClient.with_model().

Verifies that ``with_model`` produces a cloned instance with a different
model while leaving the original unchanged.
"""

import pytest

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


# --- delimiter repair -------------------------------------------------------
#
# Models drop a closing bracket at the end of a long answer. The repair pass runs
# only after strict parsing fails, so these tests have to prove both halves: that
# it recovers the real failures, and that it never touches a clean reply.

from pathlib import Path

from pydantic import BaseModel

from app.models.bi import BIReport

KNOWN_BAD = Path(__file__).parent.parent / "evals" / "fixtures" / "known_bad"


class Simple(BaseModel):
    items: list[str] = []
    name: str = ""


class TestRepairsTheRealFailure:
    """The captured artifact, not a hand-written approximation of one."""

    def test_the_fixture_still_fails_strict_json(self) -> None:
        """If this ever passes, the fixture stopped being a known-bad case."""
        import json

        raw = (KNOWN_BAD / "report_unclosed_array.json.txt").read_text()
        with pytest.raises(json.JSONDecodeError):
            json.loads(raw[raw.find("{") :])

    def test_it_recovers(self) -> None:
        raw = (KNOWN_BAD / "report_unclosed_array.json.txt").read_text()
        diagnosis: list[str] = []
        parsed = LLMClient().parse_json_response(raw, BIReport, diagnosis=diagnosis)

        assert parsed is not None, diagnosis
        assert "recovered by repairing mismatched delimiters" in diagnosis

    def test_no_data_is_lost_in_the_repair(self) -> None:
        """Closing the array must keep its last element, not truncate to it."""
        raw = (KNOWN_BAD / "report_unclosed_array.json.txt").read_text()
        parsed = LLMClient().parse_json_response(raw, BIReport)

        assert parsed is not None
        assert len(parsed.risk_factors) == 3
        assert len(parsed.recommendations) == 6
        assert "monitor cost trends" in parsed.risk_factors[-1]


class TestRepairIsALastResort:
    """A repaired parse must never pre-empt a clean one."""

    def test_valid_json_never_reports_a_repair(self) -> None:
        diagnosis: list[str] = []
        parsed = LLMClient().parse_json_response(
            '{"items": ["a", "b"], "name": "x"}', Simple, diagnosis=diagnosis
        )

        assert parsed is not None
        assert parsed.items == ["a", "b"]
        assert diagnosis == []

    def test_the_schema_echo_still_wins_over_a_repair(self) -> None:
        """The parser prefers the last *valid* object; repair must not change that."""
        parsed = LLMClient().parse_json_response(
            '{"name": "echo"} then the real one: {"name": "data"}', Simple
        )

        assert parsed is not None
        assert parsed.name == "data"

    def test_a_reply_with_no_json_is_still_unparseable(self) -> None:
        assert LLMClient().parse_json_response("no json here at all", Simple) is None


class TestRepairDiagnosisTellsTheTruth:
    """`diagnosis` is what runner.py puts in the repair turn, so a wrong
    message here is not cosmetic - it is what the model gets asked to fix.

    Caught in a live run: the note was appended before validation, so a reply
    whose delimiters repaired but whose schema still mismatched reported
    "recovered by repairing mismatched delimiters" *and* failed the stage.
    """

    def test_a_repair_that_still_fails_validation_does_not_claim_recovery(self) -> None:
        # Delimiters repairable, but `items` is a list[str] and this gives ints.
        diagnosis: list[str] = []
        parsed = LLMClient().parse_json_response(
            '{"items": [1, 2}', Simple, diagnosis=diagnosis
        )

        assert parsed is None
        assert "recovered by repairing mismatched delimiters" not in diagnosis

    def test_it_names_the_field_instead(self) -> None:
        """The repair turn needs the schema problem, not a success notice."""
        diagnosis: list[str] = []
        LLMClient().parse_json_response('{"items": [1, 2}', Simple, diagnosis=diagnosis)

        assert any("items" in d for d in diagnosis), diagnosis

    def test_a_real_recovery_still_says_so(self) -> None:
        diagnosis: list[str] = []
        parsed = LLMClient().parse_json_response(
            '{"items": ["a", "b"}', Simple, diagnosis=diagnosis
        )

        assert parsed is not None
        assert "recovered by repairing mismatched delimiters" in diagnosis


class TestRepairMechanics:
    """Directly, so the failure modes are legible without a 12KB fixture."""

    def test_array_closed_with_a_brace(self) -> None:
        parsed = LLMClient().parse_json_response('{"items": ["a", "b"}', Simple)

        assert parsed is not None
        assert parsed.items == ["a", "b"]

    def test_truncated_reply_is_closed_in_order(self) -> None:
        parsed = LLMClient().parse_json_response('{"items": ["a", "b"', Simple)

        assert parsed is not None
        assert parsed.items == ["a", "b"]

    def test_an_unterminated_string_is_closed(self) -> None:
        parsed = LLMClient().parse_json_response('{"name": "half a sen', Simple)

        assert parsed is not None
        assert parsed.name == "half a sen"

    def test_braces_inside_strings_are_not_counted(self) -> None:
        """The scanner above counts braces in prose; the repair must not."""
        parsed = LLMClient().parse_json_response(
            '{"name": "a } and a { in prose", "items": ["x"}', Simple
        )

        assert parsed is not None
        assert parsed.name == "a } and a { in prose"
        assert parsed.items == ["x"]

    def test_an_escaped_quote_does_not_end_the_string(self) -> None:
        parsed = LLMClient().parse_json_response(
            r'{"name": "she said \"hi\" then left", "items": ["x"}', Simple
        )

        assert parsed is not None
        assert parsed.name == 'she said "hi" then left'

    def test_repair_returns_none_when_nothing_needed_fixing(self) -> None:
        assert LLMClient._repair_delimiters('{"a": [1, 2]}') is None

    def test_nested_containers_close_innermost_first(self) -> None:
        repaired = LLMClient._repair_delimiters('{"a": {"b": ["c"')
        assert repaired == '{"a": {"b": ["c"]}}'
