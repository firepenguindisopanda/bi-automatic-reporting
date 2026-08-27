"""Unit tests for the stage critic.

The critic is the only metric with real variance, so M2 arbitration and the
`--check` confidence gate both rest on it returning a score. These tests cover
the ways it can stop doing that without anything looking broken.
"""

from typing import Any

import pytest
from pydantic import BaseModel

from app.analysis import critic as critic_module
from app.analysis.critic import (
    MAX_CRITIC_TOKENS,
    CriticVerdict,
    StageCritic,
    build_critic,
)
from app.analysis.runner import AgentSpec, StageContext
from app.llm.client import LLMClient
from app.models.bi import ScrapedContent
from tests.conftest import MockLLM

VALID_VERDICT = '{"score": 72, "verdict": "grounded but generic", "issues": ["vague"]}'

# What the model actually returned when its reasoning was cut off by a token
# budget too small to reach the JSON. Truncated mid-sentence, no object at all.
TRUNCATED_REASONING = (
    "We need to evaluate the analysis against the source material. The source is "
    "the Stripe homepage content. Let us examine each claimed offering in turn and "
    "check whether it appears in the page text. Online and in-person payments: yes, "
    "the source mentions accepting payments online and in person. Subscription "
    "billing: yes. Card issuing: yes. Agentic commerce: the source mentions "
    "monetising through agentic com"
)


class Dummy(BaseModel):
    name: str = "Acme"


def _spec() -> AgentSpec[Dummy]:
    return AgentSpec(
        key="profile",
        output_model=Dummy,
        system_prompt="",
        build_user_prompt=lambda ctx: "",
        model="test-model",
        prompt_version="test/1",
    )


def _ctx() -> StageContext:
    return StageContext(
        url="https://example.com",
        scraped=ScrapedContent(
            url="https://example.com",
            title="Example",
            description="An example",
            text_content="Example sells widgets to small businesses.",
        ),
    )


@pytest.fixture
def patched_client(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Capture how the critic builds its client, and script the reply."""
    seen: dict[str, Any] = {}

    def factory(**kwargs: Any) -> LLMClient:
        seen.update(kwargs)
        client = LLMClient(model=kwargs.get("model"), max_tokens=kwargs.get("max_tokens"))
        client._llm = MockLLM(responses=[seen.get("_reply", VALID_VERDICT)])
        seen["client"] = client
        return client

    monkeypatch.setattr(critic_module, "LLMClient", factory)
    return seen


class TestTokenBudget:
    """The budget that made the critic look alive while returning nothing."""

    def test_asks_for_the_declared_budget(self, patched_client: dict[str, Any]) -> None:
        import asyncio

        asyncio.run(StageCritic().review(_spec(), _ctx(), Dummy()))

        assert patched_client["max_tokens"] == MAX_CRITIC_TOKENS

    def test_budget_leaves_room_for_reasoning_before_json(self) -> None:
        """1024 was not enough for a reasoning model to finish thinking.

        Pinning the floor rather than the exact value: the point is headroom,
        not this specific number.
        """
        assert MAX_CRITIC_TOKENS >= 4096


class TestTruncatedReasoningIsUnparseable:
    """Reproduces the failure shape deterministically, without a model."""

    def test_reasoning_with_no_json_does_not_parse(self) -> None:
        diagnosis: list[str] = []
        parsed = LLMClient().parse_json_response(
            TRUNCATED_REASONING, CriticVerdict, diagnosis=diagnosis
        )

        assert parsed is None
        assert "no JSON object found in the response" in diagnosis

    def test_an_object_cut_mid_json_is_now_recovered(self) -> None:
        """This asserted `is None` until the delimiter repair landed.

        A reply cut mid-JSON is closed and salvaged now, which is strictly
        better for a critic that gets one attempt with no repair turn. The
        failure that still cannot be recovered is the one above: reasoning
        truncated *before* any JSON, where there is nothing to close.
        """
        parsed = LLMClient().parse_json_response(
            '{"score": 72, "verdict": "grounded but gener', CriticVerdict
        )

        assert parsed is not None
        assert parsed.score == 72
        assert parsed.verdict == "grounded but gener"

    def test_the_same_verdict_parses_once_it_is_complete(self) -> None:
        parsed = LLMClient().parse_json_response(
            TRUNCATED_REASONING + "\n" + VALID_VERDICT, CriticVerdict
        )

        assert parsed is not None
        assert parsed.score == 72


# Captured verbatim from the replacement critic. Valid, complete JSON that the
# original `list[str]` schema rejected outright - about one call in four.
OBJECT_SHAPED_ISSUES = """{
  "score": "78",
  "verdict": "Solid analysis with minor unsupported specifics that need correction.",
  "issues": [
    {
      "issue": "Offerings list includes 'Agentic commerce enablement (Agentic Commerce Protocol)' which invents a specific protocol name not mentioned in the source.",
      "fix": "Remove the protocol name or rephrase to 'Agentic commerce enablement'."
    },
    {
      "issue": "Offerings list includes 'Developer APIs, SDKs, and MCP server for custom integrations' which introduces an 'MCP server' not referenced in the source.",
      "fix": "Remove the MCP server reference."
    }
  ]
}"""


class TestIssueShapeTolerance:
    """The critic answers `issues` in more shapes than it was asked for."""

    def test_object_shaped_issues_parse(self) -> None:
        diagnosis: list[str] = []
        parsed = LLMClient().parse_json_response(
            OBJECT_SHAPED_ISSUES, CriticVerdict, diagnosis=diagnosis
        )

        assert parsed is not None, diagnosis
        assert parsed.score == 78
        assert len(parsed.issues) == 2

    def test_the_stated_fix_survives_flattening(self) -> None:
        """These strings become the brief for the retry, so the fix is the part
        worth keeping."""
        parsed = LLMClient().parse_json_response(OBJECT_SHAPED_ISSUES, CriticVerdict)

        assert parsed is not None
        assert "Agentic Commerce Protocol" in parsed.issues[0]
        assert "Remove the protocol name" in parsed.issues[0]

    def test_plain_string_issues_are_untouched(self) -> None:
        parsed = CriticVerdict.model_validate(
            {"score": 50, "issues": ["too vague", "no sources"]}
        )

        assert parsed.issues == ["too vague", "no sources"]

    def test_a_bare_string_becomes_one_issue(self) -> None:
        parsed = CriticVerdict.model_validate({"score": 50, "issues": "too vague"})

        assert parsed.issues == ["too vague"]

    def test_object_with_unknown_keys_keeps_its_text(self) -> None:
        parsed = CriticVerdict.model_validate(
            {"score": 50, "issues": [{"headline": "invented a competitor"}]}
        )

        assert parsed.issues == ["invented a competitor"]

    def test_score_as_a_string_still_validates(self) -> None:
        """The critic returns `"score": "78"` more often than not."""
        assert CriticVerdict.model_validate({"score": "78"}).score == 78

    def test_out_of_range_score_is_still_rejected(self) -> None:
        """Tolerance is about shape, not about accepting nonsense values."""
        with pytest.raises(ValueError):
            CriticVerdict.model_validate({"score": 140})


class TestAdvisoryOnly:
    """SPEC section 9: a critic failure never loses a result."""

    def test_unparseable_reply_returns_none_not_raises(
        self, patched_client: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        patched_client["_reply"] = TRUNCATED_REASONING
        result = asyncio.run(StageCritic().review(_spec(), _ctx(), Dummy()))

        assert result is None

    def test_valid_reply_returns_score_verdict_issues(
        self, patched_client: dict[str, Any]
    ) -> None:
        import asyncio

        result = asyncio.run(StageCritic().review(_spec(), _ctx(), Dummy()))

        assert result is not None
        score, verdict, issues = result
        assert score == 72.0
        assert verdict == "grounded but generic"
        assert issues == ["vague"]

    def test_disabled_critic_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import asyncio

        from app.config import settings

        monkeypatch.setattr(settings, "enable_stage_critic", False)

        assert asyncio.run(StageCritic().review(_spec(), _ctx(), Dummy())) is None
        assert build_critic() is None
