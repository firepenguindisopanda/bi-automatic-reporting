"""Tests for the agent runner: retry ladder, scoring, and critic handling."""

import asyncio
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, Field

from app.analysis.runner import (
    AgentRunner,
    AgentSpec,
    StageContext,
    StageError,
    classify,
)
from app.llm.client import LLMClient
from app.pipeline.contracts import StageContract, min_items, required
from app.pipeline.stages import ErrorKind


class Sample(BaseModel):
    company_name: str = ""
    offerings: list[str] = Field(default_factory=list)


VALID = '{"company_name": "Acme", "offerings": ["widgets", "gadgets", "gizmos"]}'
THIN = '{"company_name": "", "offerings": []}'
BETTER = '{"company_name": "Better", "offerings": ["widgets", "gadgets", "gizmos"]}'
GARBAGE = "Sure! Here is the analysis you asked for, but not as JSON."


class ScriptedLLM:
    """Replays a scripted list of replies or exceptions, one per call."""

    def __init__(self, script: list[Any]) -> None:
        self.script = script
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any]) -> Any:
        self.calls.append(messages)
        item = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(item, Exception):
            raise item

        class Msg:
            content = item
            usage_metadata = {"input_tokens": 10, "output_tokens": 5}

        return Msg()


def make_runner(script: list[Any], **kwargs: Any) -> tuple[AgentRunner, ScriptedLLM]:
    scripted = ScriptedLLM(script)

    def factory(model: str, temperature: float, max_tokens: int) -> LLMClient:
        client = LLMClient(model="test-model")
        client._llm = scripted  # type: ignore[assignment]
        return client

    return AgentRunner(client_factory=factory, **kwargs), scripted


def spec(**kwargs: Any) -> AgentSpec[Sample]:
    defaults: dict[str, Any] = {
        "key": "profile",
        "output_model": Sample,
        "system_prompt": "You are a test agent.",
        "build_user_prompt": lambda ctx: "Analyse this.",
        "model": "test-model",
        "prompt_version": "test/1",
        "critic": False,
    }
    return AgentSpec(**{**defaults, **kwargs})


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Backoff is tested by call count, not by actually waiting."""

    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)


class TestHappyPath:
    async def test_single_attempt(self) -> None:
        runner, llm = make_runner([VALID])
        result = await runner.run(spec(), StageContext())
        assert result.value.company_name == "Acme"
        assert result.metrics.attempts == 1
        assert len(llm.calls) == 1

    async def test_records_tokens_and_model(self) -> None:
        runner, _ = make_runner([VALID])
        result = await runner.run(spec(), StageContext())
        assert result.metrics.prompt_tokens == 10
        assert result.metrics.completion_tokens == 5
        assert result.metrics.model == "test-model"
        assert result.metrics.prompt_version == "test/1"

    async def test_duration_recorded(self) -> None:
        runner, _ = make_runner([VALID])
        result = await runner.run(spec(), StageContext())
        assert result.metrics.duration_ms >= 0


class TestRepairTurn:
    async def test_malformed_then_valid_succeeds_on_second_attempt(self) -> None:
        runner, llm = make_runner([GARBAGE, VALID])
        result = await runner.run(spec(), StageContext())
        assert result.value.company_name == "Acme"
        assert result.metrics.attempts == 2
        assert len(llm.calls) == 2

    async def test_repair_turn_feeds_back_the_bad_output(self) -> None:
        runner, llm = make_runner([GARBAGE, VALID])
        await runner.run(spec(), StageContext())
        second = llm.calls[1]
        rendered = " ".join(str(m.content) for m in second)
        assert GARBAGE in rendered
        assert "could not be used" in rendered

    async def test_third_attempt_uses_reduced_schema(self) -> None:
        runner, llm = make_runner([GARBAGE, GARBAGE, VALID])
        result = await runner.run(spec(), StageContext())
        assert result.metrics.attempts == 3
        assert len(llm.calls) == 3

    async def test_tokens_accumulate_across_attempts(self) -> None:
        runner, _ = make_runner([GARBAGE, VALID])
        result = await runner.run(spec(), StageContext())
        assert result.metrics.prompt_tokens == 20
        assert result.metrics.completion_tokens == 10


class TestPermanentFailure:
    async def test_raises_stage_error_with_parse_kind(self) -> None:
        runner, llm = make_runner([GARBAGE])
        with pytest.raises(StageError) as excinfo:
            await runner.run(spec(), StageContext())
        assert excinfo.value.error_kind == ErrorKind.PARSE_ERROR
        assert excinfo.value.stage == "profile"
        assert len(llm.calls) == 3  # exhausted max_attempts

    async def test_error_carries_last_output_for_diagnosis(self) -> None:
        runner, _ = make_runner([GARBAGE])
        with pytest.raises(StageError) as excinfo:
            await runner.run(spec(), StageContext())
        assert excinfo.value.last_output is not None
        assert GARBAGE in excinfo.value.last_output

    async def test_respects_max_attempts(self) -> None:
        runner, llm = make_runner([GARBAGE])
        with pytest.raises(StageError):
            await runner.run(spec(max_attempts=1), StageContext())
        assert len(llm.calls) == 1


class TestFailureMetrics:
    """A stage that gives up still reports what it spent getting there."""

    async def test_parse_failure_carries_metrics(self) -> None:
        runner, _ = make_runner([GARBAGE])
        with pytest.raises(StageError) as excinfo:
            await runner.run(spec(), StageContext())
        metrics = excinfo.value.metrics
        assert metrics is not None
        assert metrics.model == "test-model"
        assert metrics.attempts == 3
        assert metrics.prompt_tokens == 30  # 10 per attempt
        assert metrics.completion_tokens == 15
        assert metrics.duration_ms >= 0

    async def test_transport_failure_carries_metrics(self) -> None:
        runner, _ = make_runner([httpx.ConnectError("down")])
        with pytest.raises(StageError) as excinfo:
            await runner.run(spec(), StageContext())
        assert excinfo.value.metrics is not None
        assert excinfo.value.metrics.model == "test-model"

    async def test_non_retryable_failure_carries_metrics(self) -> None:
        runner, _ = make_runner([ValueError("bad prompt construction")])
        with pytest.raises(StageError) as excinfo:
            await runner.run(spec(), StageContext())
        assert excinfo.value.metrics is not None
        assert excinfo.value.metrics.attempts == 1

    async def test_context_error_has_no_metrics_to_report(self) -> None:
        """Nothing ran, so there is nothing to attach - and that must not crash."""
        with pytest.raises(StageError) as excinfo:
            StageContext().require_profile()
        assert excinfo.value.metrics is None


class TestTransportErrors:
    async def test_timeout_is_retried_then_succeeds(self) -> None:
        runner, llm = make_runner([httpx.ReadTimeout("slow"), VALID])
        result = await runner.run(spec(), StageContext())
        assert result.value.company_name == "Acme"
        assert len(llm.calls) == 2

    async def test_transport_failure_does_not_consume_a_prompt_attempt(self) -> None:
        """A dropped connection says nothing about the prompt.

        One timeout then two malformed replies should still get all three
        prompt attempts, rather than the timeout eating one.
        """
        runner, llm = make_runner([httpx.ConnectError("reset"), GARBAGE, GARBAGE, VALID])
        result = await runner.run(spec(), StageContext())
        assert result.value.company_name == "Acme"
        assert len(llm.calls) == 4

    async def test_persistent_transport_failure_raises(self) -> None:
        runner, _ = make_runner([httpx.ConnectError("down")])
        with pytest.raises(StageError) as excinfo:
            await runner.run(spec(), StageContext())
        assert excinfo.value.error_kind == ErrorKind.UPSTREAM_ERROR

    async def test_non_retryable_error_fails_immediately(self) -> None:
        runner, llm = make_runner([ValueError("bad prompt construction")])
        with pytest.raises(StageError) as excinfo:
            await runner.run(spec(), StageContext())
        assert excinfo.value.error_kind == ErrorKind.INTERNAL
        assert len(llm.calls) == 1


class TestClassification:
    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (httpx.ReadTimeout("x"), ErrorKind.TIMEOUT),
            (asyncio.TimeoutError(), ErrorKind.TIMEOUT),
            (httpx.ConnectError("x"), ErrorKind.UPSTREAM_ERROR),
            (ConnectionResetError(), ErrorKind.UPSTREAM_ERROR),
            (RuntimeError("upstream returned 503"), ErrorKind.HTTP_ERROR),
            (RuntimeError("request timed out upstream"), ErrorKind.TIMEOUT),
            (ValueError("nonsense"), ErrorKind.INTERNAL),
        ],
    )
    def test_classification(self, exc: Exception, expected: ErrorKind) -> None:
        assert classify(exc) == expected


class TestContractScoring:
    async def test_complete_output_scores_full(self) -> None:
        contract = StageContract(
            rules=(required("company_name"), min_items("offerings", 3)),
        )
        runner, _ = make_runner([VALID])
        result = await runner.run(spec(contract=contract), StageContext())
        assert result.metrics.completeness == 100.0
        assert result.metrics.warnings == []

    async def test_thin_output_scores_low_with_warnings(self) -> None:
        contract = StageContract(
            rules=(required("company_name"), min_items("offerings", 3)),
        )
        runner, _ = make_runner([THIN])
        result = await runner.run(spec(contract=contract), StageContext())
        assert result.metrics.completeness == 0.0
        assert len(result.metrics.warnings) == 2

    async def test_placeholder_leakage_is_penalised(self) -> None:
        runner, _ = make_runner(['{"company_name": "<string>", "offerings": ["a"]}'])
        result = await runner.run(spec(), StageContext())
        assert result.metrics.completeness == 75.0
        assert any("placeholder" in w for w in result.metrics.warnings)


class FakeCritic:
    """Replays scripted scores, one per review call.

    A bare float scores every call the same, as before. A list is what
    arbitration needs: a different score for the re-run than for the original.
    """

    def __init__(
        self,
        score: float | list[float],
        issues: list[str],
        fail: bool = False,
        fail_on_call: int | None = None,
    ) -> None:
        self.scores = [float(score)] if isinstance(score, int | float) else [float(s) for s in score]
        self.issues = issues
        self.fail = fail
        self.fail_on_call = fail_on_call
        self.calls = 0

    async def review(self, spec: Any, ctx: Any, value: Any) -> Any:
        self.calls += 1
        if self.fail or self.calls == self.fail_on_call:
            raise RuntimeError("critic exploded")
        score = self.scores[min(self.calls - 1, len(self.scores) - 1)]
        return score, "verdict text", self.issues


class TestCritic:
    async def test_high_score_keeps_result_without_rerun(self) -> None:
        critic = FakeCritic(95.0, [])
        runner, llm = make_runner([VALID], critic=critic)
        result = await runner.run_with_review(spec(critic=True), StageContext())
        assert result.metrics.confidence == 95.0
        assert len(llm.calls) == 1

    async def test_low_score_triggers_exactly_one_rerun(self) -> None:
        critic = FakeCritic(30.0, ["too generic", "no evidence"])
        runner, llm = make_runner([VALID], critic=critic)
        await runner.run_with_review(spec(critic=True), StageContext())
        assert len(llm.calls) == 2
        # Two reviews: the original and the re-run. The re-run is scored so the
        # arbitration has evidence, but is never itself re-run.
        assert critic.calls == 2

    async def test_rerun_prompt_carries_the_issues(self) -> None:
        critic = FakeCritic(30.0, ["competitor names look invented"])
        runner, llm = make_runner([VALID], critic=critic)
        await runner.run_with_review(spec(critic=True), StageContext())
        rendered = " ".join(str(m.content) for m in llm.calls[1])
        assert "competitor names look invented" in rendered

    async def test_duration_covers_the_reviews_and_the_rerun(self) -> None:
        """It used to cover only the kept run, a third of a reviewed stage's real time."""
        import time as clock

        class SlowCritic(FakeCritic):
            async def review(self, spec: Any, ctx: Any, value: Any) -> Any:
                clock.sleep(0.06)  # asyncio.sleep is stubbed out in this module
                return await super().review(spec, ctx, value)

        critic = SlowCritic([30.0, 40.0], ["too generic"])
        runner, _ = make_runner([VALID], critic=critic)
        result = await runner.run_with_review(spec(critic=True), StageContext())
        assert critic.calls == 2
        assert result.metrics.duration_ms >= 120

    async def test_critic_failure_keeps_the_result(self) -> None:
        critic = FakeCritic(0, [], fail=True)
        runner, _ = make_runner([VALID], critic=critic)
        result = await runner.run_with_review(spec(critic=True), StageContext())
        assert result.value.company_name == "Acme"
        assert result.metrics.confidence is None

    async def test_failed_rerun_keeps_the_original(self) -> None:
        critic = FakeCritic(30.0, ["thin"])
        runner, _ = make_runner([VALID, GARBAGE], critic=critic)
        result = await runner.run_with_review(spec(critic=True), StageContext())
        assert result.value.company_name == "Acme"
        assert result.metrics.confidence == 30.0

    async def test_worse_rerun_is_discarded(self) -> None:
        contract = StageContract(rules=(required("company_name"), min_items("offerings", 3)))
        critic = FakeCritic(30.0, ["thin"])
        runner, _ = make_runner([VALID, THIN], critic=critic)
        result = await runner.run_with_review(spec(critic=True, contract=contract), StageContext())
        assert result.value.company_name == "Acme"
        assert result.metrics.completeness == 100.0


class TestRetryArbitration:
    """The re-run is kept on evidence, never on a constant.

    Completeness is pinned at 100.0 in practice, so the old
    `retried.completeness < result.completeness` test was always False and
    accepted every re-run unchecked.
    """

    async def test_worse_retry_is_rejected(self) -> None:
        critic = FakeCritic([30.0, 20.0], ["thin"])
        runner, _ = make_runner([VALID, BETTER], critic=critic)
        result = await runner.run_with_review(spec(critic=True), StageContext())
        assert result.value.company_name == "Acme"
        assert result.metrics.confidence == 30.0

    async def test_better_retry_is_accepted_and_carries_its_own_score(self) -> None:
        """The kept score must describe the kept output.

        The old code wrote the original's score onto the retry it kept, so the
        stored confidence described output that had been thrown away.
        """
        critic = FakeCritic([30.0, 80.0], ["thin"])
        runner, _ = make_runner([VALID, BETTER], critic=critic)
        result = await runner.run_with_review(spec(critic=True), StageContext())
        assert result.value.company_name == "Better"
        assert result.metrics.confidence == 80.0

    async def test_a_tie_keeps_the_original(self) -> None:
        critic = FakeCritic([30.0, 30.0], ["thin"])
        runner, _ = make_runner([VALID, BETTER], critic=critic)
        result = await runner.run_with_review(spec(critic=True), StageContext())
        assert result.value.company_name == "Acme"

    async def test_critic_failure_during_arbitration_keeps_the_original(self) -> None:
        """An unscoreable replacement is not evidence of improvement."""
        critic = FakeCritic(30.0, ["thin"], fail_on_call=2)
        runner, _ = make_runner([VALID, BETTER], critic=critic)
        result = await runner.run_with_review(spec(critic=True), StageContext())
        assert result.value.company_name == "Acme"
        assert result.metrics.confidence == 30.0
        assert result.metrics.retry_confidence is None

    async def test_both_scores_are_recorded(self) -> None:
        critic = FakeCritic([30.0, 80.0], ["thin"])
        runner, _ = make_runner([VALID, BETTER], critic=critic)
        result = await runner.run_with_review(spec(critic=True), StageContext())
        assert result.metrics.confidence_before_retry == 30.0
        assert result.metrics.retry_confidence == 80.0
        assert result.metrics.critic_note is not None
        assert "30 -> 80" in result.metrics.critic_note

    async def test_a_stage_that_never_retried_records_neither(self) -> None:
        """Absent is not the same as unchanged - these stay NULL on the row."""
        critic = FakeCritic(95.0, [])
        runner, _ = make_runner([VALID], critic=critic)
        result = await runner.run_with_review(spec(critic=True), StageContext())
        assert result.metrics.confidence_before_retry is None
        assert result.metrics.retry_confidence is None

    async def test_discarded_attempt_still_counts_towards_cost(self) -> None:
        """Both attempts were paid for, whichever one is returned."""
        critic = FakeCritic([30.0, 20.0], ["thin"])
        runner, _ = make_runner([VALID, BETTER], critic=critic)
        result = await runner.run_with_review(spec(critic=True), StageContext())
        assert result.value.company_name == "Acme"
        assert result.metrics.attempts == 2

    async def test_critic_skipped_when_spec_opts_out(self) -> None:
        critic = FakeCritic(10.0, ["bad"])
        runner, _ = make_runner([VALID], critic=critic)
        result = await runner.run_with_review(spec(critic=False), StageContext())
        assert critic.calls == 0
        assert result.metrics.confidence is None


class TestStageContext:
    def test_require_raises_when_missing(self) -> None:
        with pytest.raises(StageError):
            StageContext().require_profile()

    def test_require_returns_value_when_present(self) -> None:
        from app.models.bi import BusinessProfile

        profile = BusinessProfile(
            company_name="Acme", tagline="", description="", industry="", value_proposition=""
        )
        assert StageContext(profile=profile).require_profile().company_name == "Acme"


class TestFailureDiagnosis:
    """An intermittent failure is only fixable if the record says what broke.

    "no valid X found" reads the same for malformed JSON and for a schema
    mismatch. These assert the two are distinguishable after the fact.
    """

    async def test_schema_mismatch_names_the_field(self) -> None:
        # `offerings` is list[str]; the model answers with objects.
        wrong_shape = '{"company_name": "Acme", "offerings": [{"name": "widget"}]}'
        runner, _ = make_runner([wrong_shape])
        with pytest.raises(StageError) as excinfo:
            await runner.run(spec(), StageContext())
        assert "offerings" in excinfo.value.message
        assert excinfo.value.error_kind == ErrorKind.PARSE_ERROR

    async def test_malformed_json_is_reported_as_such(self) -> None:
        """A syntax error the delimiter repair cannot reach.

        This used to use `"offerings": [}`, which the repair now closes into an
        empty list - the stage succeeds and the empty list shows up as a low
        contract score instead. A missing comma between keys is genuinely
        unparseable, so it still exercises the diagnosis path.
        """
        runner, _ = make_runner(['{"company_name": "Acme" "offerings": ["x"]}'])
        with pytest.raises(StageError) as excinfo:
            await runner.run(spec(), StageContext())
        message = excinfo.value.message.lower()
        assert "invalid json" in message or "no json object" in message

    async def test_a_mismatched_bracket_is_repaired_rather_than_failed(self) -> None:
        """The layered contract: parse leniently, score strictly.

        `[}` closes into an empty list rather than losing the whole stage. The
        empty list is not hidden - it lands in the contract score, which is
        where thin output is supposed to be judged.
        """
        runner, _ = make_runner(['{"company_name": "Acme", "offerings": [}'])
        result = await runner.run(spec(), StageContext())
        assert result.value.company_name == "Acme"
        assert result.value.offerings == []

    async def test_no_json_at_all_says_so(self) -> None:
        runner, _ = make_runner(["I'm afraid I can't help with that."])
        with pytest.raises(StageError) as excinfo:
            await runner.run(spec(), StageContext())
        assert "no json object" in excinfo.value.message.lower()

    async def test_the_repair_turn_carries_the_specific_reason(self) -> None:
        """Feeding back a vague error teaches the model nothing."""
        runner, llm = make_runner(['{"company_name": "Acme", "offerings": [{"a": 1}]}', VALID])
        await runner.run(spec(), StageContext())
        repair = " ".join(str(m.content) for m in llm.calls[1])
        assert "offerings" in repair

    def test_diagnosis_is_optional(self) -> None:
        """Callers that do not care keep the old two-argument form."""
        client = LLMClient(model="test-model")
        assert client.parse_json_response(VALID, Sample) is not None
        assert client.parse_json_response("nonsense", Sample) is None


class TruncatingLLM(ScriptedLLM):
    """Like ScriptedLLM, but reports finish_reason for the replies it is told to."""

    def __init__(self, script: list[Any], cut_off: set[int]) -> None:
        super().__init__(script)
        self.cut_off = cut_off

    async def ainvoke(self, messages: list[Any]) -> Any:
        msg = await super().ainvoke(messages)
        msg.response_metadata = {
            "finish_reason": "length" if len(self.calls) - 1 in self.cut_off else "stop"
        }
        return msg


class TestTransportBudget:
    """Transport retries do not compete with the repair ladder for attempts."""

    async def test_a_timeout_after_two_bad_replies_is_still_retried(self) -> None:
        runner, llm = make_runner([GARBAGE, GARBAGE, httpx.ReadTimeout("slow"), VALID])
        result = await runner.run(spec(), StageContext())
        assert result.value.company_name == "Acme"
        assert len(llm.calls) == 4

    async def test_attempts_count_every_request_sent(self) -> None:
        runner, _ = make_runner([httpx.ConnectError("reset"), GARBAGE, VALID])
        result = await runner.run(spec(), StageContext())
        assert result.metrics.attempts == 3

    async def test_persistent_failure_stops_at_the_transport_budget(self) -> None:
        runner, llm = make_runner([httpx.ConnectError("down")])
        with pytest.raises(StageError) as excinfo:
            await runner.run(spec(max_transport_retries=2), StageContext())
        assert len(llm.calls) == 3  # the first try plus two retries
        assert excinfo.value.metrics is not None
        assert excinfo.value.metrics.attempts == 3

    async def test_no_backoff_is_wasted_before_giving_up(self, monkeypatch) -> None:
        sleeps: list[float] = []

        async def record(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", record)
        runner, _ = make_runner([httpx.ConnectError("down")])
        with pytest.raises(StageError):
            await runner.run(spec(max_transport_retries=2), StageContext())
        assert len(sleeps) == 2

    async def test_backoff_grows_and_carries_jitter(self, monkeypatch) -> None:
        sleeps: list[float] = []

        async def record(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", record)
        runner, _ = make_runner([httpx.ConnectError("down")])
        with pytest.raises(StageError):
            await runner.run(spec(max_transport_retries=3), StageContext())
        assert 1.5 <= sleeps[0] <= 1.5 * 1.25
        assert 3.0 <= sleeps[1] <= 3.0 * 1.25
        assert sleeps[0] < sleeps[1] < sleeps[2]


class TestHttpStatusHandling:
    @pytest.mark.parametrize(
        "exc",
        [
            Exception("[401] Unauthorized\nPlease check or regenerate your API key."),
            Exception("[404] Not Found\nmodel retired"),
            Exception("[400] Bad Request\ncontext length exceeded"),
        ],
    )
    async def test_client_errors_fail_at_once(self, exc: Exception) -> None:
        runner, llm = make_runner([exc, VALID])
        with pytest.raises(StageError) as excinfo:
            await runner.run(spec(), StageContext())
        assert excinfo.value.error_kind == ErrorKind.HTTP_ERROR
        assert len(llm.calls) == 1

    @pytest.mark.parametrize(
        "exc",
        [
            Exception("[429] Too Many Requests"),
            Exception("[503] Service Unavailable"),
            Exception("[408] Request Timeout"),
        ],
    )
    async def test_throttling_and_server_errors_are_retried(self, exc: Exception) -> None:
        runner, llm = make_runner([exc, VALID])
        result = await runner.run(spec(), StageContext())
        assert result.value.company_name == "Acme"
        assert len(llm.calls) == 2

    def test_status_is_read_from_an_http_error(self) -> None:
        from app.analysis.runner import http_status

        request = httpx.Request("POST", "https://example.invalid")
        error = httpx.HTTPStatusError(
            "nope", request=request, response=httpx.Response(404, request=request)
        )
        assert http_status(error) == 404
        assert http_status(Exception("[502] Bad Gateway")) == 502
        assert http_status(ValueError("nothing here")) is None


class TestTruncation:
    def _runner(self, script: list[Any], cut_off: set[int]) -> AgentRunner:
        scripted = TruncatingLLM(script, cut_off)

        def factory(model: str, temperature: float, max_tokens: int) -> LLMClient:
            client = LLMClient(model="test-model")
            client._llm = scripted  # type: ignore[assignment]
            return client

        return AgentRunner(client_factory=factory)

    async def test_a_reply_cut_off_at_the_limit_is_flagged(self) -> None:
        cut = '{"company_name": "Acme", "offerings": ["widgets", "gadgets", "giz'
        runner = self._runner([cut], cut_off={0})
        contract = StageContract(rules=(required("company_name"),))
        result = await runner.run(spec(contract=contract), StageContext())
        assert result.metrics.completeness < 100.0
        assert any("cut off" in w for w in result.metrics.warnings)

    async def test_a_complete_reply_is_not_flagged(self) -> None:
        runner = self._runner([VALID], cut_off=set())
        result = await runner.run(spec(), StageContext())
        assert not any("cut off" in w for w in result.metrics.warnings)

    async def test_an_unparseable_cut_off_reply_says_why(self) -> None:
        runner = self._runner(["{\"company_name\": ", VALID], cut_off={0})
        await runner.run(spec(), StageContext())
        # The repair turn names the token limit, not just a JSON error.
        repair = runner._client_factory("m", 0.0, 1)._llm.calls[1]  # type: ignore[attr-defined]
        assert "token limit" in " ".join(str(m.content) for m in repair)
