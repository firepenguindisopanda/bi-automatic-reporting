"""Stage-targeted assertions for eval cases.

An entry under a case's ``assertions:`` names the stage(s) it checks, an
optional dotted field path, and one or more checks:

    - stage: market                 # or `stages: [profile, marketing]`
      path: pricing_strategy        # optional; lists are walked element-wise
      forbid: [...]                 # none of these literals may appear
      forbid_regex: [...]           # none of these patterns may match
      require_any: [...]            # at least one of these literals ...
      require_any_regex: [...]      # ... or patterns must appear

Checks read every string *value* in the stage output (or under ``path``) and
never the field names, so they hold whatever shape the output model takes.
Matching is case-insensitive throughout. Pure: no app imports, no I/O.
"""

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

CHECK_KEYS = ("forbid", "forbid_regex", "require_any", "require_any_regex")
ASSERTION_KEYS = frozenset({"stage", "stages", "path", *CHECK_KEYS})


def plain(value: Any) -> Any:
    """A stage output as plain dicts and lists, whatever model produced it."""
    return value.model_dump() if isinstance(value, BaseModel) else value


def iter_strings(value: Any) -> Iterator[str]:
    """Every string *value* anywhere in a nested structure - never the keys."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from iter_strings(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from iter_strings(v)


def select(data: Any, parts: Sequence[str]) -> tuple[list[Any], bool]:
    """The values at a dotted path, and whether the path exists at all.

    A list is walked element by element, so ``key_players.name`` is every
    player's name. An empty list or a null field is found-but-empty: that is the
    model's answer, not a schema mismatch.
    """
    if not parts:
        return [data], True
    if data is None:
        return [], True
    if isinstance(data, list | tuple):
        out: list[Any] = []
        found = not data
        for item in data:
            values, hit = select(item, parts)
            out.extend(values)
            found = found or hit
        return out, found
    if isinstance(data, dict) and parts[0] in data:
        return select(data[parts[0]], parts[1:])
    return [], False


def _snippet(text: str, match: re.Match[str]) -> str:
    start, end = max(0, match.start() - 25), min(len(text), match.end() + 25)
    return " ".join(text[start:end].split())


@dataclass(frozen=True)
class StageAssertion:
    """One expectation about the text of one or more stages' output."""

    stages: tuple[str, ...]
    path: str | None = None
    forbid: tuple[str, ...] = ()
    forbid_regex: tuple[re.Pattern[str], ...] = ()
    require_any: tuple[str, ...] = ()
    require_any_regex: tuple[re.Pattern[str], ...] = ()

    def applies_to(self, stage: str) -> bool:
        return stage in self.stages

    def evaluate(self, stage: str, value: Any) -> list[str]:
        """Failure messages, one per forbidden hit and one for an unmet require_any."""
        where = f"{stage}.{self.path}" if self.path else stage
        data = plain(value)
        values, found = select(data, self.path.split(".")) if self.path else ([data], True)
        if not found:
            # Loud on purpose: an assertion that silently stops matching after
            # an output model changes is worse than one that fails.
            return [f"[{where}] path not found in the {stage} output"]

        text = "\n".join(s for v in values for s in iter_strings(v))
        folded = text.casefold()
        failures: list[str] = []
        for term in self.forbid:
            if term.casefold() in folded:
                failures.append(f"[{where}] contains forbidden {term!r}")
        for pattern in self.forbid_regex:
            match = pattern.search(text)
            if match:
                failures.append(f"[{where}] matches forbidden /{pattern.pattern}/ in {_snippet(text, match)!r}")
        if self.require_any or self.require_any_regex:
            hit = any(t.casefold() in folded for t in self.require_any) or any(
                p.search(text) for p in self.require_any_regex
            )
            if not hit:
                wanted = [*self.require_any, *(f"/{p.pattern}/" for p in self.require_any_regex)]
                failures.append(f"[{where}] mentions none of {wanted}")
        return failures


def _string_list(raw: Any, key: str, where: str) -> tuple[str, ...]:
    items = [raw] if isinstance(raw, str) else raw
    if not isinstance(items, list) or not items or not all(isinstance(i, str) and i for i in items):
        raise ValueError(f"{where}: {key} must be a non-empty string or list of strings")
    return tuple(items)


def _compile(patterns: tuple[str, ...], where: str) -> tuple[re.Pattern[str], ...]:
    try:
        return tuple(re.compile(p, re.IGNORECASE) for p in patterns)
    except re.error as e:
        raise ValueError(f"{where}: bad regex {e.pattern!r}: {e}") from e


def parse_assertion(
    raw: Any, stage_keys: Sequence[str] | None = None, where: str = "assertion"
) -> StageAssertion:
    """Validate one ``assertions:`` entry. ``stage_keys`` limits the stages it may name."""
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: must be a mapping, got {type(raw).__name__}")
    unknown = set(raw) - ASSERTION_KEYS
    if unknown:
        raise ValueError(f"{where}: unknown key(s) {sorted(unknown)}; allowed: {sorted(ASSERTION_KEYS)}")
    if ("stage" in raw) == ("stages" in raw):
        raise ValueError(f"{where}: give exactly one of `stage` or `stages`")
    stages = _string_list(raw.get("stage", raw.get("stages")), "stage(s)", where)
    if stage_keys is not None:
        bad = [s for s in stages if s not in stage_keys]
        if bad:
            raise ValueError(f"{where}: unknown stage(s) {bad}; this suite has {list(stage_keys)}")
    if not any(k in raw for k in CHECK_KEYS):
        raise ValueError(f"{where}: needs at least one of {list(CHECK_KEYS)}")
    path = raw.get("path")
    if path is not None and (not isinstance(path, str) or not all(path.split("."))):
        raise ValueError(f"{where}: path must be a dotted field path like 'key_players.name'")

    def strings(key: str) -> tuple[str, ...]:
        return _string_list(raw[key], key, where) if key in raw else ()

    return StageAssertion(
        stages=stages,
        path=path,
        forbid=strings("forbid"),
        forbid_regex=_compile(strings("forbid_regex"), where),
        require_any=strings("require_any"),
        require_any_regex=_compile(strings("require_any_regex"), where),
    )


def parse_assertions(case: dict[str, Any], stage_keys: Sequence[str] | None = None) -> list[StageAssertion]:
    """Every stage assertion in a case; raises ValueError naming the case and entry."""
    raw = case.get("assertions") or []
    if not isinstance(raw, list):
        raise ValueError(f"case {case.get('name')!r}: assertions must be a list")
    return [
        parse_assertion(entry, stage_keys, f"case {case.get('name')!r} assertion #{i + 1}")
        for i, entry in enumerate(raw)
    ]
