"""LLM review of stage output.

The contract score in ``app.pipeline.contracts`` catches structural weakness -
empty fields, short lists, placeholder text. It cannot tell whether a
well-formed answer is actually about this business, or whether the competitor
names were invented. That is what this pass is for.

It is advisory and optional. A critic failure never loses a result, and the
whole pass is disabled by ``ENABLE_STAGE_CRITIC=false``.
"""

import logging
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.analysis import prompts
from app.analysis.runner import AgentSpec, StageContext
from app.config import settings
from app.llm.client import LLMClient

logger = logging.getLogger(__name__)

# As much of the page as any analyst is given (the profile stage reads 15,000
# characters). At 4,000 the critic could not see facts the analyst had read
# further down - Valcrest's prices sit at 5,400-6,500 and its email at 9,700 - and
# scored a profile that quoted them correctly as fabricated, 30-50 for the most
# thorough answer. That ranked models backwards: the less of the page an answer
# used, the more of it the critic could verify.
MAX_SOURCE_CHARS = 15000
# Compact JSON, so the same budget covers about a third more of the output. A
# stage that still overruns is cut with an explicit note: a silently truncated
# output reads as an incomplete one, and the critic marked it down for that.
MAX_OUTPUT_CHARS = 8000
TRUNCATION_NOTE = "\n[output truncated for review - the remainder exists and is not missing]"

# The critic runs on a reasoning model, which spends completion tokens thinking
# before it emits any JSON. At 1024 the thinking was cut off mid-sentence and the
# response contained no JSON object at all, so every call raised and every stage
# scored no confidence - a dead metric that still looked alive. Measured against a
# real profile-stage prompt: reasoning alone overran 1024, and the same prompt
# parsed cleanly at 4096.
MAX_CRITIC_TOKENS = 4096


# The keys the critic uses when it returns an issue as an object rather than a
# string, in the order they read best once flattened: what is wrong, then what to
# do about it.
_ISSUE_KEYS = ("issue", "problem", "description", "detail", "text")
_FIX_KEYS = ("fix", "suggestion", "recommendation", "remedy", "action")


class CriticVerdict(BaseModel):
    score: int = Field(ge=0, le=100)
    verdict: str = ""
    issues: list[str] = Field(default_factory=list)

    @field_validator("issues", mode="before")
    @classmethod
    def _flatten_issues(cls, value: Any) -> Any:
        """Accept the richer shapes the critic reaches for unprompted.

        Asked for `list[str]`, the model sometimes answers with objects -
        `{"issue": ..., "fix": ...}` - which is valid JSON that fails validation,
        so the whole verdict was discarded and the stage recorded no confidence
        at all. Measured at roughly one call in four against a real prompt. The
        extra field is worth keeping rather than rejecting: these strings are fed
        back to the model as the brief for its retry, so a stated fix is the most
        actionable part of the issue.
        """
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return value

        flattened: list[Any] = []
        for item in value:
            if isinstance(item, dict):
                parts = [
                    str(item[k]).strip()
                    for k in (*_ISSUE_KEYS, *_FIX_KEYS)
                    if isinstance(item.get(k), str | int | float) and str(item[k]).strip()
                ]
                # An object with none of the known keys still beats dropping the
                # verdict: keep whatever strings it does carry.
                if not parts:
                    parts = [
                        str(v).strip()
                        for v in item.values()
                        if isinstance(v, str | int | float) and str(v).strip()
                    ]
                flattened.append(" - ".join(parts) if parts else "")
            else:
                flattened.append(item)
        return flattened


class StageCritic:
    """Reviews one stage's output for groundedness, specificity and actionability."""

    def __init__(self, model: str | None = None) -> None:
        self._model = model or settings.agent_model_critic

    async def review(
        self, spec: AgentSpec[Any], ctx: StageContext, value: BaseModel
    ) -> tuple[float, str, list[str]] | None:
        if not settings.enable_stage_critic:
            return None

        client = LLMClient(model=self._model, temperature=0.0, max_tokens=MAX_CRITIC_TOKENS)
        try:
            verdict = await client.ainvoke_structured(
                output_model=CriticVerdict,
                system_prompt=prompts.CRITIC,
                user_prompt=self._build_prompt(spec, ctx, value),
            )
        except Exception:  # noqa: BLE001 - advisory only
            logger.warning("Critic call failed for stage %s", spec.key, exc_info=True)
            return None

        issues = [i.strip() for i in verdict.issues if i.strip()][:5]
        return float(verdict.score), verdict.verdict.strip(), issues

    def _build_prompt(self, spec: AgentSpec[Any], ctx: StageContext, value: BaseModel) -> str:
        output = value.model_dump_json()
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + TRUNCATION_NOTE
        return (
            f"ANALYSIS STAGE: {spec.key}\n\n"
            f"SOURCE MATERIAL THE ANALYST WAS GIVEN:\n{self._source(spec, ctx)}\n\n"
            f"ANALYSIS THEY PRODUCED:\n{output}\n\n"
            "Review this analysis and return your score, verdict and issues."
        )

    def _source(self, spec: AgentSpec[Any], ctx: StageContext) -> str:
        """The grounding material, trimmed to keep the critic prompt bounded."""
        if spec.critic_source is not None:
            return spec.critic_source(ctx)
        if ctx.scraped is not None:
            s = ctx.scraped
            return (
                f"URL: {s.url}\nTITLE: {s.title}\nDESCRIPTION: {s.description}\n\n"
                f"PAGE CONTENT:\n{s.text_content[:MAX_SOURCE_CHARS]}"
            )
        if ctx.market_query:
            return (
                f"MARKET QUERY: {ctx.market_query}\n\n"
                "There is no source document for market research: judge against "
                "well-established general knowledge, and treat unverifiable "
                "specifics (invented URLs, precise figures with no basis) as defects."
            )
        return "(no source material was provided to this stage)"


def build_critic() -> StageCritic | None:
    """The configured critic, or None when the pass is switched off."""
    return StageCritic() if settings.enable_stage_critic else None
