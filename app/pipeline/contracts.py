"""Deterministic quality scoring for stage output.

Every stage declares what a complete answer looks like. The result is scored,
never enforced: thin output still produces a report, it just scores low and
carries warnings. That keeps a mediocre run useful while making its weakness
visible, which a hard validation error would not.

Scoring costs nothing and runs on every stage. The LLM critic in
``app.analysis.critic`` is the separate, optional, paid signal.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
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

# Similarity thresholds, measured against the stored `Self-hosted CI/CD tools`
# resources output rather than guessed. On that artifact:
#
#   relevance vs its own description - templated entries cluster at 0.65-0.70,
#   entries that say something real top out at 0.54. 0.60 sits in the gap.
#
#   description vs description - the reused sentence scores 0.94-1.00 across
#   pairs, so 0.85 catches it with room to spare.
#
# Both are token-sequence ratios, not character ones: two sentences differing
# only in the product name are ~95% identical by character and that similarity
# is precisely what needs catching, so characters would be too blunt to tune.
FIELD_ECHO_THRESHOLD = 0.60
NEAR_IDENTICAL_THRESHOLD = 0.85

PLACEHOLDER_PENALTY = 25.0
DUPLICATE_PENALTY = 10.0
ECHOED_EXAMPLE_PENALTY = 30.0
TRUNCATION_MARKER_PENALTY = 10.0
TRUNCATION_PENALTY = 20.0
"""Applied by the runner when the provider reports the reply hit max_tokens."""

# The example schema renders a scalar as its field title in angle brackets -
# "<Company Name>" - so a model that echoes the example returns exactly that.
# A whole value in angle brackets is never a real answer.
_BRACKETED_PLACEHOLDER = re.compile(r"^<[^<>]{1,80}>$")

# Text that ends mid-thought. A long field trailing off in an ellipsis, or
# carrying an explicit marker, was cut short rather than finished.
_TRUNCATION_MARKERS = re.compile(r"(\.\.\.|…)\s*$|\[(?:truncated|continued|cut off)\]", re.IGNORECASE)
_TRUNCATION_MIN_CHARS = 40

# Words that say what kind of entity a company is, not which one. "Acme
# Corporation" is grounded by "Acme" appearing on the page, not by "corporation".
_COMPANY_SUFFIXES = frozenset(
    {
        "inc", "inc.", "corp", "corp.", "corporation", "company", "co", "co.", "ltd", "ltd.",
        "limited", "llc", "plc", "gmbh", "group", "holdings", "the", "and", "technologies",
        "technology", "labs", "software", "solutions", "services", "systems",
    }
)

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


def max_items(path: str, count: int, label: str | None = None) -> Rule:
    """List must carry at most ``count`` entries.

    The prompts ask for bounded lists - "3-5 direct competitors", "2-4
    personas" - and a model padding past the bound is usually inventing the
    extra entries. Missing lists are ``min_items``' job, not this one.
    """
    name = label or path

    def rule(value: BaseModel) -> str | None:
        got = _get(value, path)
        if isinstance(got, list) and len(got) > count:
            return f"{name} has {len(got)} items, expected at most {count}"
        return None

    return rule


def min_words(path: str, count: int, label: str | None = None) -> Rule:
    """String must run to at least ``count`` words - catches one-word categories."""
    name = label or path

    def rule(value: BaseModel) -> str | None:
        got = _get(value, path)
        if not isinstance(got, str) or not got.strip():
            return None  # emptiness is `required`'s job
        if len(got.split()) < count:
            return f"{name} {got.strip()[:40]!r} is too generic, expected at least {count} words"
        return None

    return rule


def matches(path: str, pattern: str, explanation: str, label: str | None = None) -> Rule:
    """String must match ``pattern`` (searched, case-insensitive)."""
    name = label or path
    compiled = re.compile(pattern, re.IGNORECASE)

    def rule(value: BaseModel) -> str | None:
        got = _get(value, path)
        if not isinstance(got, str) or not got.strip():
            return None  # emptiness is `required`'s job
        if compiled.search(got) is None:
            return f"{name} {got.strip()[:50]!r} {explanation}"
        return None

    return rule


# A market size the prompt would accept: a range ("$2-5B", "1.2 to 3 billion")
# or a TAM/SAM/SOM breakdown. A single precise figure is what it forbids.
RANGE_PATTERN = (
    r"\d[\d.,]*\s*(?:[kmbt]|bn|mn|million|billion|trillion)?\s*(?:-|–|—|to)\s*"
    r"[$€£]?\s*\d"
    r"|\b(?:tam|sam|som)\b"
)


def items_have_any(
    path: str, alternatives: Iterable[str], what: str, label: str | None = None
) -> Rule:
    """Every entry must carry at least one of ``alternatives``, non-empty.

    For a field the model names inconsistently - a competitor's offering comes
    back as ``offering`` or ``description`` - where either counts.
    """
    name = label or path
    keys = tuple(alternatives)

    def rule(value: BaseModel) -> str | None:
        got = _get(value, path)
        if not isinstance(got, list) or not got:
            return None  # emptiness is `min_items`' job
        for i, item in enumerate(got):
            if not any(_field(item, key) for key in keys):
                return f"{name}[{i}] is missing {what}"
        return None

    return rule


def items_min_list(path: str, field: str, count: int, label: str | None = None) -> Rule:
    """Every entry's ``field`` list must carry at least ``count`` entries."""
    name = label or path

    def rule(value: BaseModel) -> str | None:
        got = _get(value, path)
        if not isinstance(got, list) or not got:
            return None  # emptiness is `min_items`' job
        short = []
        for i, item in enumerate(got):
            inner = item.get(field) if isinstance(item, dict) else getattr(item, field, None)
            if not isinstance(inner, list) or len(inner) < count:
                short.append(str(i))
        if short:
            return f"{name} entries {', '.join(short[:5])} have fewer than {count} {field}"
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
        if _BRACKETED_PLACEHOLDER.match(stripped):
            hits.append(f"placeholder text {text.strip()[:60]!r}")
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


def find_truncation_markers(value: BaseModel) -> list[str]:
    """Long text fields that stop mid-thought.

    The bare placeholder check already catches a value that is only "...";
    this is for a real sentence that trails off, which is what a reply cut
    short and then repaired into valid JSON looks like.
    """
    hits: list[str] = []
    for text in _iter_strings(value.model_dump()):
        stripped = text.strip()
        if len(stripped) >= _TRUNCATION_MIN_CHARS and _TRUNCATION_MARKERS.search(stripped):
            hits.append(f"text appears cut off: {stripped[-50:]!r}")
    return hits


def find_echoed_examples(value: BaseModel) -> list[str]:
    """Fields returned verbatim from a prompt's own example."""
    hits: list[str] = []
    for text in _iter_strings(value.model_dump()):
        if text.strip().lower() in PROMPT_EXAMPLE_LITERALS:
            hits.append(f"copied the prompt's example instead of answering: {text.strip()!r}")
    return hits


def _string_lists(value: Any, path: str = "") -> Iterable[tuple[str, list[str]]]:
    """Every list of plain strings in a nested structure, with its dotted path."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield from _string_lists(inner, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list) and value:
        if all(isinstance(x, str) for x in value):
            yield path, value
        else:
            for i, inner in enumerate(value):
                yield from _string_lists(inner, f"{path}[{i}]")


def find_duplicate_list_entries(value: BaseModel) -> list[str]:
    """Repeated entries inside any list of plain strings, at any depth.

    Nested lists matter as much as top-level ones: a persona's pain points or a
    journey stage's touchpoints padded with a repeat is the same failure.
    """
    warnings: list[str] = []
    for path, raw in _string_lists(value.model_dump()):
        normalised = [x.strip().lower() for x in raw if x.strip()]
        if len(normalised) != len(set(normalised)):
            warnings.append(f"{path} contains duplicate entries")
    return warnings


def _tokens(text: str) -> list[str]:
    """Lowercased alphanumeric word tokens. Punctuation and case are noise here."""
    return re.findall(r"[a-z0-9]+", text.lower())


def similarity(a: str, b: str) -> float:
    """0-1 token-sequence similarity. Standard library only - see SPEC.md section 4."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return SequenceMatcher(None, ta, tb).ratio()


def _field(item: Any, key: str) -> str:
    """One field off a dict or a model, as text."""
    raw = item.get(key) if isinstance(item, dict) else getattr(item, key, "")
    return str(raw or "").strip()


def no_cross_list_duplicates(
    paths: Iterable[str], key: str = "name", label: str | None = None
) -> Rule:
    """The same entity listed under two different categories.

    ``no_duplicate_names`` only looks inside a single list, so an entity
    repeated across ``key_players`` and ``competitor_sites`` was invisible.
    That is how one resources output shipped 21 entries carrying 18 distinct
    names and still scored 100.

    A repeat *within* one list is deliberately left alone - that is the other
    rule's job, and reporting it twice would penalise it twice.
    """
    wanted = tuple(paths)
    name = label or " / ".join(wanted)

    def rule(value: BaseModel) -> str | None:
        first_seen: dict[str, str] = {}
        clashes: list[str] = []
        for path in wanted:
            got = _get(value, path)
            if not isinstance(got, list):
                continue
            for item in got:
                text = _field(item, key)
                if not text:
                    continue
                folded = text.lower()
                where = first_seen.get(folded)
                if where is None:
                    first_seen[folded] = path
                elif where != path:
                    clashes.append(f"{text!r} in both {where} and {path}")
        if clashes:
            return f"{name} repeat the same entity: " + "; ".join(sorted(set(clashes))[:3])
        return None

    return rule


def field_differs_from(
    path: str,
    a: str,
    b: str,
    threshold: float = FIELD_ECHO_THRESHOLD,
    label: str | None = None,
) -> Rule:
    """One field restating another, e.g. ``relevance`` echoing ``description``.

    Both fields are non-empty and look specific, so nothing else notices: the
    output passes ``items_have`` and reads as complete while the second field
    carries no information the first did not.
    """
    name = label or path

    def rule(value: BaseModel) -> str | None:
        got = _get(value, path)
        if not isinstance(got, list):
            return None
        echoes: list[str] = []
        for item in got:
            first, second = _field(item, a), _field(item, b)
            if not first or not second:
                continue  # emptiness is `items_have`'s job, not this one
            score = similarity(first, second)
            if score >= threshold:
                echoes.append(f"{_field(item, 'name') or '?'} ({score:.2f})")
        if echoes:
            return (
                f"{name}: {b} restates {a} in {len(echoes)} entr"
                f"{'y' if len(echoes) == 1 else 'ies'} - " + ", ".join(echoes[:3])
            )
        return None

    return rule


def no_near_identical_items(
    path: str,
    threshold: float = NEAR_IDENTICAL_THRESHOLD,
    field: str = "description",
    label: str | None = None,
) -> Rule:
    """Entries padded out from one templated sentence.

    Compares a single field rather than the whole entry: the name and URL
    legitimately differ between entries, and including them dilutes exactly the
    similarity this is looking for. ``description`` is where the template shows.
    """
    name = label or path

    def rule(value: BaseModel) -> str | None:
        got = _get(value, path)
        if not isinstance(got, list) or len(got) < 2:
            return None
        texts = [(_field(item, "name"), _field(item, field)) for item in got]
        texts = [(n, text) for n, text in texts if text]
        pairs: list[str] = []
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                score = similarity(texts[i][1], texts[j][1])
                if score >= threshold:
                    pairs.append(f"{texts[i][0] or '?'}/{texts[j][0] or '?'} ({score:.2f})")
        if pairs:
            return (
                f"{name}: {len(pairs)} pair(s) share a near-identical {field} - "
                + ", ".join(pairs[:3])
            )
        return None

    return rule


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
        if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack):
            return None
        # Accept a partial match so "Acme Corporation Ltd" still counts when
        # the page only ever says "Acme" - but only on the distinctive words.
        # Matching any word let "Globex Software" pass on a page that merely
        # mentions software.
        words = [
            w for w in re.findall(r"[\w'&.-]+", needle)
            if len(w) >= 3 and w not in _COMPANY_SUFFIXES
        ]
        if words and any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", haystack) for w in words):
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

            cut_off = find_truncation_markers(value)
            if cut_off:
                warnings.extend(cut_off[:3])
                score -= TRUNCATION_MARKER_PENALTY

        if self.check_duplicates:
            dupes = find_duplicate_list_entries(value)
            if dupes:
                warnings.extend(dupes)
                score -= DUPLICATE_PENALTY

        return ContractResult(completeness=max(0.0, round(score, 1)), warnings=warnings)


# A stage with no declared expectations. Scores 100 minus the universal checks.
EMPTY_CONTRACT = StageContract()
