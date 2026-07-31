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

from pydantic import BaseModel, Field

from app.analysis import prompts
from app.analysis.runner import AgentSpec, StageContext
from app.config import settings
from app.llm.client import LLMClient

logger = logging.getLogger(__name__)

MAX_SOURCE_CHARS = 4000
MAX_OUTPUT_CHARS = 6000


class CriticVerdict(BaseModel):
    score: int = Field(ge=0, le=100)
    verdict: str = ""
    issues: list[str] = Field(default_factory=list)


class StageCritic:
    """Reviews one stage's output for groundedness, specificity and actionability."""

    def __init__(self, model: str | None = None) -> None:
        self._model = model or settings.agent_model_critic

    async def review(
        self, spec: AgentSpec[Any], ctx: StageContext, value: BaseModel
    ) -> tuple[float, str, list[str]] | None:
        if not settings.enable_stage_critic:
            return None

        client = LLMClient(model=self._model, temperature=0.0, max_tokens=1024)
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
        return (
            f"ANALYSIS STAGE: {spec.key}\n\n"
            f"SOURCE MATERIAL THE ANALYST WAS GIVEN:\n{self._source(ctx)}\n\n"
            f"ANALYSIS THEY PRODUCED:\n{value.model_dump_json(indent=2)[:MAX_OUTPUT_CHARS]}\n\n"
            "Review this analysis and return your score, verdict and issues."
        )

    def _source(self, ctx: StageContext) -> str:
        """The grounding material, trimmed - the critic runs on a small model."""
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
