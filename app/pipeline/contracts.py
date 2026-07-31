"""Deterministic quality scoring for stage output.

Every stage declares what a complete answer looks like. The result is scored,
never enforced: thin output still produces a report, it just scores low and
carries warnings. That keeps a mediocre run useful while making its weakness
visible, which a hard validation error would not.

Scoring costs nothing and runs on every stage. The LLM critic in
``app.analysis.critic`` is the separate, optional, paid signal.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

# A rule returns a warning string when it is not satisfied, else None.
Rule = Callable[[BaseModel], str | None]

# Some checks need the source material, not just the output - for instance,
# confirming an extracted company name actually appears on the page.
ContextRule = Callable[[BaseModel, Any], str | None]

# Strings that indicate the model echoed the prompt scaffolding instead of
# answering. This is the documented failure mode of example-schema prompting.
PLACEHOLDER_MARKERS = (
    "<string>",
    "<integer>",
    "<number>",
    "<boolean>",
    "<array>",
    "<object>",
    "...",
    "n/a",
    "not available",
    "not applicable",
    "todo",
    "lorem ipsum",
    "your answer here",
    "example value",
)

PLACEHOLDER_PENALTY = 25.0
DUPLICATE_PENALTY = 10.0
ECHOED_EXAMPLE_PENALTY = 30.0

# Literal examples that appear inside the prompts. A model that returns one has
# copied the instruction rather than answered it - and because the value looks
# specific and is non-empty, no other check notices. Found by the eval harness:
# Eleventy, a static site generator, came back as "SaaS E-commerce Analytics".
#
# Keep in step with app/analysis/prompts.py.
PROMPT_EXAMPLE_LITERALS = frozenset(
    {
        "saas e-commerce analytics",
        "sme retailers with 10-50 employees",
        "$500m-$2b",
        "strong team",
        "good product",
        "bad support",
        "technology",
    }
)


@dataclass(frozen=True, slots=True)
class ContractResult:
    completeness: float
    """0-100. Higher is a more complete answer, not necessarily a more correct one."""
    warnings: list[str]


def _get(value: BaseModel, path: str) -> Any:
    """Read a dotted attribute path, returning None if any hop is missing."""
    current: Any = value
    for part in path.split("."):
        if current is None:
            return None
        current = getattr(current, part, None)
    return current


def required(path: str, label: str | None = None) -> Rule:
    """Field must be present and non-empty."""
    name = label or path

    def rule(value: BaseModel) -> str | None:
        got = _get(value, path)
        if got is None or (isinstance(got, str | list | dict) and len(got) == 0):
            return f"{name} is empty"
        return None

    return rule


def min_items(path: str, count: int, label: str | None = None) -> Rule:
    """List must carry at least ``count`` entries."""
    name = label or path

    def rule(value: BaseModel) -> str | None:
        got = _get(value, path)
        if not isinstance(got, list):
            return f"{name} is missing"
        if len(got) < count:
            return f"{name} has {len(got)} items, expected at least {count}"
        return None

    return rule


def min_length(path: str, chars: int, label: str | None = None) -> Rule:
    """String must be at least ``chars`` long - catches one-line stubs."""
    name = label or path

    def rule(value: BaseModel) -> str | None:
        got = _get(value, path)
        if not isinstance(got, str):
            return f"{name} is missing"
        if len(got.strip()) < chars:
            return f"{name} is only {len(got.strip())} characters, expected at least {chars}"
        return None

    return rule


def items_have(path: str, keys: Iterable[str], label: str | None = None) -> Rule:
    """Every entry in a list of dicts/models must carry these keys, non-empty."""
    name = label or path
    wanted = tuple(keys)

    def rule(value: BaseModel) -> str | None:
        got = _get(value, path)
        if not isinstance(got, list) or not got:
            return f"{name} is empty"
        for i, item in enumerate(got):
            for key in wanted:
                found = item.get(key) if isinstance(item, dict) else getattr(item, key, None)
                if not found:
                    return f"{name}[{i}] is missing {key}"
        return None

    return rule


def no_duplicate_names(path: str, key: str = "name", label: str | None = None) -> Rule:
    """Catches a model padding a list by repeating the same entry."""
    name = label or path

    def rule(value: BaseModel) -> str | None:
        got = _get(value, path)
        if not isinstance(got, list):
            return None
        seen = [
            str(item.get(key) if isinstance(item, dict) else getattr(item, key, "")).lower().strip()
            for item in got
        ]
        seen = [s for s in seen if s]
        if len(seen) != len(set(seen)):
            return f"{name} contains duplicate entries"
        return None

    return rule


def _iter_strings(value: Any) -> Iterable[str]:
    """Every string anywhere in a nested structure."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_strings(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _iter_strings(v)


def find_placeholders(value: BaseModel) -> list[str]:
    """Placeholder or schema-echo text left in the output."""
    hits: list[str] = []
    for text in _iter_strings(value.model_dump()):
        stripped = text.strip().lower()
        if not stripped:
            continue
        for marker in PLACEHOLDER_MARKERS:
            # Exact-match short markers so a legitimate sentence containing
            # "not applicable" mid-flow is not punished; substring-match the
            # angle-bracket ones, which are never legitimate.
            hit = marker in stripped if marker.startswith("<") else stripped == marker
            if hit:
                hits.append(f"placeholder text {text.strip()[:60]!r}")
                break
    return hits


def find_echoed_examples(value: BaseModel) -> list[str]:
    """Fields returned verbatim from a prompt's own example."""
    hits: list[str] = []
    for text in _iter_strings(value.model_dump()):
        if text.strip().lower() in PROMPT_EXAMPLE_LITERALS:
            hits.append(f"copied the prompt's example instead of answering: {text.strip()!r}")
    return hits


def find_duplicate_list_entries(value: BaseModel) -> list[str]:
    """Repeated entries inside any list of plain strings."""
    warnings: list[str] = []
    for field_name, raw in value.model_dump().items():
        if isinstance(raw, list) and raw and all(isinstance(x, str) for x in raw):
            normalised = [x.strip().lower() for x in raw if x.strip()]
            if len(normalised) != len(set(normalised)):
                warnings.append(f"{field_name} contains duplicate entries")
    return warnings


def grounded_in_source(path: str, label: str | None = None) -> ContextRule:
    """Value must actually appear in the scraped page text.

    Catches the worst extraction failure: a confidently reported company name
    that is nowhere on the site.
    """
    name = label or path

    def rule(value: BaseModel, ctx: Any) -> str | None:
        got = _get(value, path)
        if not isinstance(got, str) or not got.strip():
            return None  # emptiness is the `required` rule's job, not this one
        scraped = getattr(ctx, "scraped", None)
        if scraped is None:
            return None
        haystack = " ".join(
            [
                getattr(scraped, "text_content", "") or "",
                getattr(scraped, "title", "") or "",
                getattr(scraped, "description", "") or "",
                getattr(scraped, "url", "") or "",
            ]
        ).lower()
        if not haystack.strip():
            return None
        needle = got.strip().lower()
        if needle in haystack:
            return None
        # Accept a partial match so "Acme Corporation Ltd" still counts when
        # the page only ever says "Acme".
        words = [w for w in needle.split() if len(w) > 3]
        if words and any(w in haystack for w in words):
            return None
        return f"{name} {got.strip()[:40]!r} does not appear in the page content"

    return rule


@dataclass(frozen=True)
class StageContract:
    """What a complete answer looks like for one stage."""

    rules: tuple[Rule, ...] = ()
    context_rules: tuple[ContextRule, ...] = ()
    check_placeholders: bool = True
    check_duplicates: bool = True

    def evaluate(self, value: BaseModel, ctx: Any = None) -> ContractResult:
        warnings: list[str] = []

        failures = 0
        for rule in self.rules:
            warning = rule(value)
            if warning is not None:
                failures += 1
                warnings.append(warning)

        for context_rule in self.context_rules:
            warning = context_rule(value, ctx)
            if warning is not None:
                failures += 1
                warnings.append(warning)

        total = len(self.rules) + len(self.context_rules)
        score = 100.0
        if total:
            score -= (failures / total) * 100.0

        if self.check_placeholders:
            hits = find_placeholders(value)
            if hits:
                warnings.extend(hits[:5])
                score -= PLACEHOLDER_PENALTY

            echoed = find_echoed_examples(value)
            if echoed:
                warnings.extend(echoed[:3])
                score -= ECHOED_EXAMPLE_PENALTY

        if self.check_duplicates:
            dupes = find_duplicate_list_entries(value)
            if dupes:
                warnings.extend(dupes)
                score -= DUPLICATE_PENALTY

        return ContractResult(completeness=max(0.0, round(score, 1)), warnings=warnings)


# A stage with no declared expectations. Scores 100 minus the universal checks.
EMPTY_CONTRACT = StageContract()
