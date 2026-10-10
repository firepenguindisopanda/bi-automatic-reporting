"""One runner for every agent.

Agents are declarative: a spec names the model, prompt, output schema and
quality contract, and this runner does the rest - retries, repair turns,
scoring, and metric capture. That replaces six near-identical agent classes,
each of which carried its prompt twice and had no retry at all.

Retry ladder, per attempt:
  1. normal request
  2. repair turn - the invalid reply and the parse error are fed back
  3. clean retry - a fresh request without the failed exchange, in case the
     repair context itself is what the model is stuck on

Transport failures (timeout, connection reset, 408/429/5xx) are retried with
exponential backoff and jitter on their own budget, since they say nothing
about the prompt and must not use up a step of the ladder. Other 4xx responses
(bad key, retired model, oversized request) fail at once: repeating the same
request cannot fix them.
"""

import asyncio
import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from app.llm.client import LLMClient
from app.models.bi import (
    BusinessProfile,
    CompetitiveAnalysis,
    MarketAnalysis,
    MarketingAnalysis,
    ReportSynthesis,
    ScrapedContent,
    SWOTAnalysis,
)
from app.pipeline.contracts import EMPTY_CONTRACT, TRUNCATION_PENALTY, StageContract
from app.pipeline.stages import ErrorKind

logger = logging.getLogger(__name__)

BACKOFF_BASE_SECONDS = 1.5
BACKOFF_MAX_SECONDS = 20.0


class StageError(RuntimeError):
    """A stage that could not be completed, with enough detail to show the user."""

    def __init__(
        self,
        stage: str,
        error_kind: ErrorKind,
        message: str,
        last_output: str | None = None,
        metrics: "StageMetrics | None" = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.error_kind = error_kind
        self.message = message
        # Trimmed: this ends up in a database column and a log line.
        self.last_output = (last_output or "")[:2000] or None
        # What the stage spent before giving up. A failure is precisely when
        # the model, attempt count and token cost are worth knowing, so they
        # travel with the error instead of dying with the local scope.
        self.metrics = metrics


_STATUS_PREFIX = re.compile(r"^\s*\[(\d{3})\]")
_STATUS_IN_TEXT = re.compile(r"\b(?:status(?: code)?:?\s*|http\s*)(\d{3})\b", re.IGNORECASE)
RETRYABLE_STATUSES = frozenset({408, 425, 429})


def http_status(exc: BaseException) -> int | None:
    """The HTTP status behind an upstream error, when it can be recovered.

    ChatNVIDIA raises a bare ``Exception("[404] Not Found ...")`` rather than an
    HTTP error type, so the status has to be read back out of the message.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    text = str(exc)
    match = _STATUS_PREFIX.match(text) or _STATUS_IN_TEXT.search(text)
    return int(match.group(1)) if match else None


def is_retryable(exc: BaseException, kind: ErrorKind) -> bool:
    """Whether repeating the identical request could plausibly succeed."""
    if kind not in RETRYABLE_KINDS:
        return False
    status = http_status(exc)
    if status is None:
        return True
    return status >= 500 or status in RETRYABLE_STATUSES


def classify(exc: BaseException) -> ErrorKind:
    """Map an exception to the error kind shown alongside the failed stage."""
    if isinstance(exc, asyncio.TimeoutError | httpx.TimeoutException):
        return ErrorKind.TIMEOUT
    if isinstance(exc, httpx.HTTPStatusError) or http_status(exc) is not None:
        return ErrorKind.HTTP_ERROR
    if isinstance(exc, httpx.TransportError | ConnectionError | OSError):
        return ErrorKind.UPSTREAM_ERROR
    # LangChain wraps provider errors in its own types; fall back to the text.
    text = f"{type(exc).__name__} {exc}".lower()
    if "timeout" in text or "timed out" in text:
        return ErrorKind.TIMEOUT
    if any(w in text for w in ("connection", "network", "ssl", "dns", "reset")):
        return ErrorKind.UPSTREAM_ERROR
    if any(w in text for w in ("status", "rate limit", "429", "500", "502", "503")):
        return ErrorKind.HTTP_ERROR
    return ErrorKind.INTERNAL


def ms_since(started: float) -> int:
    """Milliseconds since a ``time.monotonic()`` reading; never 0 for work that ran.

    Truncating sub-millisecond work to 0 made a finished stage look unstarted
    and a job that failed fast report 0s elapsed.
    """
    return max(1, round((time.monotonic() - started) * 1000))


RETRYABLE_KINDS = frozenset(
    {ErrorKind.TIMEOUT, ErrorKind.HTTP_ERROR, ErrorKind.UPSTREAM_ERROR}
)


@dataclass
class StageContext:
    """Everything produced so far, available to a stage's prompt builder.

    Accessors raise rather than return None so a prompt builder can rely on its
    inputs without every builder repeating a null check.
    """

    url: str = ""
    market_query: str = ""
    scraped: ScrapedContent | None = None
    profile: BusinessProfile | None = None
    market: MarketAnalysis | None = None
    competitive: CompetitiveAnalysis | None = None
    swot: SWOTAnalysis | None = None
    marketing: MarketingAnalysis | None = None
    report: ReportSynthesis | None = None
    macro: BaseModel | None = None
    sizing: BaseModel | None = None
    # Populated by the critic so a re-run can address named issues.
    critic_issues: list[str] = field(default_factory=list)

    def _require(self, name: str) -> Any:
        value = getattr(self, name)
        if value is None:
            raise StageError(
                name, ErrorKind.INTERNAL, f"{name} was not available to this stage"
            )
        return value

    def require_scraped(self) -> ScrapedContent:
        return self._require("scraped")  # type: ignore[no-any-return]

    def require_profile(self) -> BusinessProfile:
        return self._require("profile")  # type: ignore[no-any-return]

    def require_market(self) -> MarketAnalysis:
        return self._require("market")  # type: ignore[no-any-return]

    def require_competitive(self) -> CompetitiveAnalysis:
        return self._require("competitive")  # type: ignore[no-any-return]

    def require_swot(self) -> SWOTAnalysis:
        return self._require("swot")  # type: ignore[no-any-return]

    def require_marketing(self) -> MarketingAnalysis:
        return self._require("marketing")  # type: ignore[no-any-return]

    def require_report(self) -> ReportSynthesis:
        return self._require("report")  # type: ignore[no-any-return]

    def require_macro(self) -> BaseModel:
        return self._require("macro")  # type: ignore[no-any-return]

    def require_sizing(self) -> BaseModel:
        return self._require("sizing")  # type: ignore[no-any-return]


@dataclass(frozen=True)
class AgentSpec[T: BaseModel]:
    """A declarative agent. One per pipeline stage that calls a model."""

    key: str
    """Stage slug, matching app.pipeline.stages."""
    output_model: type[T]
    system_prompt: str
    build_user_prompt: Callable[[StageContext], str]
    model: str
    prompt_version: str
    temperature: float = 0.0
    max_tokens: int = 4096
    max_attempts: int = 3
    """Steps of the repair ladder: normal, repair turn, clean retry."""
    max_transport_retries: int = 3
    """Backoff retries for timeouts and 408/429/5xx, separate from the ladder."""
    contract: StageContract = EMPTY_CONTRACT
    critic: bool = True
    critic_source: Callable[[StageContext], str] | None = None
    """What the critic judges groundedness against, when not the scraped page.

    The report writer is given the upstream analyses, not the page, so judging
    its synthesis against the page would penalise every recommendation that
    rests on the market or competitive stage.
    """


@dataclass
class StageMetrics:
    """What gets persisted on the stage row."""

    model: str
    prompt_version: str
    attempts: int = 0
    """Requests actually sent, including ones lost to transport errors."""
    duration_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    completeness: float = 100.0
    warnings: list[str] = field(default_factory=list)
    confidence: float | None = None
    confidence_before_retry: float | None = None
    """The first attempt's critic score, when a re-run was arbitrated.

    None when no re-run happened - absent is not the same as unchanged, and a
    stage that never retried should not look like one that retried to no effect.
    """
    retry_confidence: float | None = None
    """The re-run's own score. None when there was no re-run, or the critic
    could not score it - in which case the original is kept."""
    critic_note: str | None = None


@dataclass
class StageResult[T: BaseModel]:
    value: T
    metrics: StageMetrics


class Critic(Protocol):
    """Scores a stage's output. Implemented in app.analysis.critic."""

    async def review(
        self, spec: AgentSpec[Any], ctx: StageContext, value: BaseModel
    ) -> tuple[float, str, list[str]] | None:
        """Return (score, verdict, issues), or None when unavailable."""
        ...


class AgentRunner:
    """Executes agent specs. One instance is shared by the whole process."""

    def __init__(
        self,
        critic: Critic | None = None,
        critic_retry_threshold: float = 60.0,
        client_factory: Callable[[str, float, int], LLMClient] | None = None,
    ) -> None:
        self._critic = critic
        self._critic_retry_threshold = critic_retry_threshold
        # Injectable so tests can drive the retry ladder without a network call.
        self._client_factory = client_factory or (
            lambda model, temperature, max_tokens: LLMClient(
                model=model, temperature=temperature, max_tokens=max_tokens
            )
        )

    def _client[T: BaseModel](self, spec: AgentSpec[T]) -> LLMClient:
        return self._client_factory(spec.model, spec.temperature, spec.max_tokens)

    def _messages[T: BaseModel](
        self,
        client: LLMClient,
        spec: AgentSpec[T],
        ctx: StageContext,
        attempt: int,
        last_output: str | None,
        last_error: str | None,
    ) -> list[Any]:
        """Build the request for this attempt, escalating the repair strategy."""
        user_prompt = spec.build_user_prompt(ctx)
        if ctx.critic_issues:
            user_prompt += (
                "\n\nA reviewer rejected a previous attempt for these reasons. "
                "Address each one:\n"
                + "\n".join(f"- {issue}" for issue in ctx.critic_issues)
            )

        # Attempts 1 and 3 are both a clean request. The third deliberately
        # drops the repair exchange: a model that could not fix its own reply
        # when shown it is sometimes anchored on that reply.
        messages = client.build_json_messages(spec.output_model, spec.system_prompt, user_prompt)

        # Second attempt: show the model its own invalid reply and the problem.
        if attempt == 2 and last_output:
            messages.append(AIMessage(content=last_output[:6000]))
            messages.append(
                HumanMessage(
                    content=(
                        f"That response could not be used: {last_error}. "
                        "Return ONLY a single valid JSON object matching the schema. "
                        "No markdown fences, no commentary, no repeated schema."
                    )
                )
            )
        return messages

    async def run[T: BaseModel](self, spec: AgentSpec[T], ctx: StageContext) -> StageResult[T]:
        """Run one agent to a validated result, or raise StageError."""
        client = self._client(spec)
        metrics = StageMetrics(model=spec.model, prompt_version=spec.prompt_version)
        started = time.monotonic()

        value: T | None = None
        truncated = False
        last_output: str | None = None
        last_error: str | None = None
        transport_failures = 0

        def give_up(kind: ErrorKind, message: str) -> StageError:
            """Stamp the elapsed time and hand the metrics to the caller."""
            metrics.duration_ms = ms_since(started)
            return StageError(spec.key, kind, message, last_output, metrics)

        attempt = 0
        while attempt < spec.max_attempts:
            attempt += 1
            messages = self._messages(client, spec, ctx, attempt, last_output, last_error)
            metrics.attempts += 1
            try:
                raw = await client.acomplete(messages)
            except Exception as exc:  # noqa: BLE001 - classified immediately below
                kind = classify(exc)
                if not is_retryable(exc, kind) or transport_failures >= spec.max_transport_retries:
                    raise give_up(kind, str(exc)) from exc
                transport_failures += 1
                delay = min(
                    BACKOFF_BASE_SECONDS * (2 ** (transport_failures - 1)), BACKOFF_MAX_SECONDS
                )
                delay += random.uniform(0, delay * 0.25)  # noqa: S311 - jitter, not crypto
                logger.warning(
                    "Stage %s attempt %d/%d hit %s (%s); retry %d/%d in %.1fs",
                    spec.key, attempt, spec.max_attempts, kind, exc,
                    transport_failures, spec.max_transport_retries, delay,
                )
                await asyncio.sleep(delay)
                # A transport failure is not the prompt's fault: repeat this
                # step of the ladder rather than escalating past it.
                attempt -= 1
                continue

            metrics.prompt_tokens += raw.prompt_tokens
            metrics.completion_tokens += raw.completion_tokens

            diagnosis: list[str] = []
            parsed = client.parse_json_response(raw.content, spec.output_model, diagnosis)
            if parsed is not None:
                value = parsed
                truncated = raw.finish_reason == "length"
                break

            last_output = raw.content
            # Say *why* it could not be used. "No valid X found" is the same
            # message for malformed JSON and for a schema mismatch, which
            # makes an intermittent failure needlessly hard to chase - and
            # the repair turn is only useful if it names the actual problem.
            detail = "; ".join(diagnosis[:4]) or "no parseable object"
            if raw.finish_reason == "length":
                detail = f"the reply hit the {spec.max_tokens}-token limit and was cut off; {detail}"
            last_error = f"{spec.output_model.__name__} could not be built: {detail}"
            logger.warning(
                "Stage %s attempt %d/%d: %s", spec.key, attempt, spec.max_attempts, last_error
            )

        if value is None:
            raise give_up(
                ErrorKind.PARSE_ERROR,
                last_error or "the model returned no usable output",
            )

        result = self._score(spec, ctx, value, metrics, truncated=truncated)
        metrics.duration_ms = ms_since(started)
        return result

    def _score[T: BaseModel](
        self,
        spec: AgentSpec[T],
        ctx: StageContext,
        value: T,
        metrics: StageMetrics,
        *,
        truncated: bool = False,
    ) -> StageResult[T]:
        contract = spec.contract.evaluate(value, ctx)
        metrics.completeness = contract.completeness
        metrics.warnings = list(contract.warnings)
        if truncated:
            # The reply parsed only because the parser closed what the model
            # never finished. The shape is valid; the last field is not whole.
            metrics.completeness = max(0.0, metrics.completeness - TRUNCATION_PENALTY)
            metrics.warnings.append(
                f"reply hit the {spec.max_tokens}-token limit and was cut off; "
                "the last fields may be incomplete"
            )
        return StageResult(value=value, metrics=metrics)

    async def _review[T: BaseModel](
        self, spec: AgentSpec[T], ctx: StageContext, value: T
    ) -> tuple[float, str, list[str]] | None:
        """Score one output, or None when the critic could not.

        The house rule is that an advisory pass never loses a result, so a
        critic failure is swallowed here rather than reaching either caller.
        """
        if self._critic is None:
            return None
        try:
            return await self._critic.review(spec, ctx, value)
        except Exception:  # noqa: BLE001 - the critic is advisory, never fatal
            logger.warning("Critic failed for stage %s; keeping result", spec.key, exc_info=True)
            return None

    async def run_with_review[T: BaseModel](
        self, spec: AgentSpec[T], ctx: StageContext
    ) -> StageResult[T]:
        """Run a stage, then optionally have a critic score it.

        A low score triggers exactly one re-run. The re-run is scored too, and
        replaces the original only when it scores strictly higher - a
        replacement nobody checked is not an improvement. A critic failure
        never loses a result.
        """
        result = await self.run(spec, ctx)
        if self._critic is None or not spec.critic:
            return result

        review = await self._review(spec, ctx, result.value)
        if review is None:
            return result

        score, verdict, issues = review
        result.metrics.confidence = score
        result.metrics.critic_note = verdict

        if score >= self._critic_retry_threshold or not issues:
            return result

        logger.info(
            "Stage %s scored %.0f (below %.0f); re-running with %d reviewer issues",
            spec.key, score, self._critic_retry_threshold, len(issues),
        )
        retry_ctx = replace_critic_issues(ctx, issues)
        try:
            retried = await self.run(spec, retry_ctx)
        except StageError:
            logger.warning("Stage %s re-run failed; keeping the original result", spec.key)
            return result

        # The re-run is scored exactly once and is never itself re-run. This is
        # the only arbitration point, so there is no recursion to bound.
        #
        # Comparing completeness here instead was the original bug: it is pinned
        # at 100.0, so `retried < result` was always False and every re-run was
        # kept without a shred of evidence it was better.
        retry_review = await self._review(spec, retry_ctx, retried.value)
        retry_score, retry_verdict = (
            (retry_review[0], retry_review[1]) if retry_review is not None else (None, "")
        )

        if retry_score is not None and retry_score > score:
            kept, discarded = retried, result
            kept.metrics.confidence = retry_score
            kept.metrics.critic_note = (
                f"re-run kept ({score:.0f} -> {retry_score:.0f}): {retry_verdict}"
            )
        else:
            # Ties and an unscoreable re-run both keep the original: without
            # evidence of improvement there is no reason to swap.
            kept, discarded = result, retried
            kept.metrics.confidence = score
            reason = (
                f"{retry_score:.0f} did not beat {score:.0f}"
                if retry_score is not None
                else "the critic could not score it"
            )
            kept.metrics.critic_note = f"re-run discarded ({reason}): {verdict}"

        # The discarded attempt still cost tokens and attempts. Dropping them
        # would make the stage row understate what the stage actually spent.
        kept.metrics.attempts += discarded.metrics.attempts
        kept.metrics.prompt_tokens += discarded.metrics.prompt_tokens
        kept.metrics.completion_tokens += discarded.metrics.completion_tokens

        kept.metrics.confidence_before_retry = score
        kept.metrics.retry_confidence = retry_score
        return kept


def replace_critic_issues(ctx: StageContext, issues: list[str]) -> StageContext:
    """A shallow copy carrying reviewer feedback, leaving the original intact."""
    from dataclasses import replace

    return replace(ctx, critic_issues=issues)
