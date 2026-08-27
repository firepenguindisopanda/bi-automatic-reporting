"""LLM client wrapping ChatNVIDIA for all agent calls.

Async only. The sync path was removed: ChatNVIDIA's ``.invoke()`` returns
truncated response bodies under concurrency, which is what made structured
parsing fail intermittently (see AGENTS.md).

Structured output is produced by asking for JSON directly and parsing the
reply, not via ``with_structured_output`` - the same reason. The runner in
``app.analysis.runner`` layers retries and repair turns on top of this.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, TypeVar

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from pydantic import BaseModel, ValidationError

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# One ChatNVIDIA per distinct configuration. Previously every with_model() call
# built a new client - and so a new httpx connection pool - on every agent call.
_CHAT_CACHE: dict[tuple[str, float, int, int], ChatNVIDIA] = {}


def _get_chat(model: str, temperature: float, max_tokens: int, timeout: int) -> ChatNVIDIA:
    key = (model, temperature, max_tokens, timeout)
    chat = _CHAT_CACHE.get(key)
    if chat is None:
        chat = ChatNVIDIA(
            model=model,
            temperature=temperature,
            max_completion_tokens=max_tokens,
            timeout=timeout,
            api_key=settings.nvidia_api_key or None,
        )
        _CHAT_CACHE[key] = chat
    return chat


async def close_all() -> None:
    """Release cached upstream clients. Called from the FastAPI lifespan."""
    for chat in list(_CHAT_CACHE.values()):
        for attr in ("async_client", "_async_client", "client", "_client"):
            candidate = getattr(chat, attr, None)
            aclose = getattr(candidate, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:  # noqa: BLE001 - best effort on shutdown
                    logger.debug("Failed closing %s on cached chat client", attr, exc_info=True)
    _CHAT_CACHE.clear()


@dataclass(frozen=True, slots=True)
class RawResponse:
    """A single completion plus whatever token accounting the provider returned."""

    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _usage(message: Any) -> tuple[int, int]:
    """Pull (prompt, completion) token counts out of a LangChain response."""
    meta = getattr(message, "usage_metadata", None)
    if isinstance(meta, dict):
        return int(meta.get("input_tokens", 0)), int(meta.get("output_tokens", 0))
    response_meta = getattr(message, "response_metadata", None)
    if isinstance(response_meta, dict):
        usage = response_meta.get("token_usage") or {}
        if isinstance(usage, dict):
            return int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
    return 0, 0


class LLMClient:
    """Thin wrapper around ChatNVIDIA with support for structured outputs."""

    def __init__(
        self,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self._model = model or settings.nvidia_model
        self._temperature = settings.temperature if temperature is None else temperature
        self._max_tokens = max_tokens or settings.max_tokens
        self._llm = _get_chat(
            self._model, self._temperature, self._max_tokens, settings.llm_timeout
        )

    def with_model(
        self,
        model_id: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> "LLMClient":
        """Return a new LLMClient using a different model. Original unchanged."""
        return LLMClient(model=model_id, temperature=temperature, max_tokens=max_tokens)

    @property
    def model(self) -> str:
        return self._model

    async def acomplete(self, messages: list[Any]) -> RawResponse:
        """Send messages and return the raw reply with token counts."""
        response = await self._llm.ainvoke(messages)
        prompt_tokens, completion_tokens = _usage(response)
        content = response.content
        return RawResponse(
            content=content if isinstance(content, str) else str(content),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    async def ainvoke(
        self,
        system_prompt: str = "",
        user_prompt: str = "",
        prefill: str = "",
    ) -> str:
        """Send a prompt and return the raw text response."""
        messages: list[Any] = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        if user_prompt:
            messages.append(HumanMessage(content=user_prompt))
        if prefill:
            messages.append(AIMessage(content=prefill))
        return (await self.acomplete(messages)).content

    # --- JSON schema prompting ---------------------------------------------

    def _resolve_ref(self, prop: dict, defs: dict) -> dict:
        ref = prop.get("$ref", "")
        if ref:
            key = ref.split("/")[-1]
            resolved = defs.get(key, {})
            if resolved:
                return self._resolve_ref(resolved, defs)
        return prop

    def _resolve_anyof(self, prop: dict, defs: dict) -> dict:
        anyof = prop.get("anyOf", [])
        if anyof:
            non_null = [self._resolve_ref(a, defs) for a in anyof if a.get("type") != "null"]
            if non_null:
                return non_null[0]
        return prop

    def _example_for_prop(self, prop: dict, defs: dict, depth: int = 0, max_depth: int = 3) -> str:
        if depth > max_depth:
            return '"..."'
        prop = self._resolve_anyof(prop, defs)
        prop = self._resolve_ref(prop, defs)
        ptype = prop.get("type", "string")
        if ptype == "array":
            items = self._resolve_ref(prop.get("items", {}), defs)
            item_type = items.get("type", "string")
            nested = items.get("properties", {})
            additional = items.get("additionalProperties", {})
            if nested:
                subs = [
                    f'"{k}": {self._example_for_prop(v, defs, depth + 1, max_depth)}'
                    for k, v in nested.items()
                ]
                return "[{" + ", ".join(subs) + "}]"
            elif item_type == "object" and additional:
                return '[{"name": "...", "description": "..."}]'
            elif item_type == "object":
                return '[{"key": "value"}]'
            else:
                return '["<' + item_type + '>"]'
        elif ptype == "object":
            nested = prop.get("properties", {})
            if nested:
                subs = [
                    f'"{k}": {self._example_for_prop(v, defs, depth + 1, max_depth)}'
                    for k, v in nested.items()
                ]
                return "{" + ", ".join(subs) + "}"
            else:
                return "{}"
        else:
            title = prop.get("description", prop.get("title", ptype))
            return f'"<{title}>"'

    def build_example_schema(self, model: type[T], max_depth: int = 3) -> str:
        """Render a filled-in example of the target schema.

        Llama follows this example literally, so it must mirror the desired
        output structure exactly. ``max_depth`` is lowered on a retry to keep
        the prompt short when the model has already choked on the full one.
        """
        schema = model.model_json_schema()
        defs = schema.get("$defs", {})
        props = schema.get("properties", {})
        parts = [
            f'  "{k}": {self._example_for_prop(v, defs, 0, max_depth)}' for k, v in props.items()
        ]
        return "{\n" + ",\n".join(parts) + "\n}"

    def build_json_messages(
        self,
        output_model: type[T],
        system_prompt: str,
        user_prompt: str,
        schema_depth: int = 3,
    ) -> list[Any]:
        """Messages that ask for one JSON object matching ``output_model``."""
        example = self.build_example_schema(output_model, max_depth=schema_depth)
        instruction = (
            "\n\nRespond with ONLY valid JSON in this format "
            f"(no markdown, no explanation):\n{example}"
        )
        messages: list[Any] = []
        if system_prompt or instruction:
            messages.append(SystemMessage(content=system_prompt + instruction))
        if user_prompt:
            messages.append(HumanMessage(content=user_prompt))
        return messages

    @staticmethod
    def _repair_delimiters(text: str) -> str | None:
        """Close containers the model left mismatched, or return None.

        Models drop a closing bracket at the end of a long answer. Observed on
        `gpt-oss-20b` writing a BIReport: the last field is `risk_factors`, a
        list of strings, and the reply ends

            "risk_factors":[ "...", "...", "monitor cost trends."}

        - the array closed with `}` instead of `]}`. Everything before it, some
        12,000 characters, is valid. Measured at 3 failures in 6 calls on one
        prompt, always that same shape.

        Walks the text with a stack of open containers, ignoring braces inside
        string literals (which the scanner above does not, so a `}` in prose
        would mislead it). A closer that does not match the innermost opener
        closes the inner containers first rather than being discarded, which is
        what turns `[ ... }` into `[ ... ]}`. Anything still open at the end is
        closed in order, which also recovers a genuinely truncated reply.

        Returns None when nothing needed repairing, so callers can tell a
        repair from a no-op. This never runs unless strict parsing has already
        failed.
        """
        out: list[str] = []
        stack: list[str] = []
        closer = {"{": "}", "[": "]"}
        in_string = False
        escaped = False
        repaired = False

        for ch in text:
            if in_string:
                out.append(ch)
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                out.append(ch)
            elif ch in "{[":
                stack.append(ch)
                out.append(ch)
            elif ch in "}]":
                # Close every container the model skipped over, innermost first.
                while stack and closer[stack[-1]] != ch:
                    out.append(closer[stack.pop()])
                    repaired = True
                if stack:
                    stack.pop()
                    out.append(ch)
                else:
                    # A closer with nothing open: drop it rather than emit junk.
                    repaired = True
            else:
                out.append(ch)

        if in_string:
            out.append('"')
            repaired = True
        while stack:
            out.append(closer[stack.pop()])
            repaired = True

        return "".join(out) if repaired else None

    def parse_json_response(
        self, content: str, output_model: type[T], diagnosis: list[str] | None = None
    ) -> T | None:
        """Extract and validate the target model from a raw reply.

        Returns None when nothing in the reply parses into the model. Pass
        ``diagnosis`` to receive why: "no JSON found", a JSON syntax error, or
        the specific fields that failed validation. Without it, a schema
        mismatch and malformed JSON are indistinguishable, which turns a
        five-minute fix into an afternoon.
        """
        text = content.strip()
        text = text.removeprefix("```json").removeprefix("```").strip()
        if text.endswith("```"):
            text = text[:-3].strip()
        # Find ALL JSON objects - the model sometimes echoes the schema first.
        objects: list[str] = []
        i = 0
        while True:
            brace = text.find("{", i)
            if brace < 0:
                break
            depth = 0
            for j in range(brace, len(text)):
                c = text[j]
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        objects.append(text[brace : j + 1])
                        i = j + 1
                        break
            else:
                break
        if not objects and diagnosis is not None:
            diagnosis.append("no JSON object found in the response")

        # Take the last object that validates - the data usually follows the echo.
        for candidate in reversed(objects):
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return output_model.model_validate(data)
                if diagnosis is not None:
                    diagnosis.append(f"top-level JSON was {type(data).__name__}, expected object")
            except json.JSONDecodeError as e:
                if diagnosis is not None:
                    diagnosis.append(f"invalid JSON: {e}")
            except ValidationError as e:
                if diagnosis is not None:
                    diagnosis.extend(
                        f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}"
                        for err in e.errors()[:4]
                    )

        # Strict parsing found nothing usable. Only now try closing delimiters
        # the model mismatched - a repaired parse must never pre-empt a clean
        # one, and a reply that already parses never reaches this line.
        start = text.find("{")
        if start >= 0:
            repaired = self._repair_delimiters(text[start:])
            if repaired is not None:
                try:
                    data = json.loads(repaired)
                except json.JSONDecodeError:
                    data = None
                if isinstance(data, dict):
                    try:
                        parsed = output_model.model_validate(data)
                    except ValidationError as e:
                        # Repairing the delimiters worked; the schema still does
                        # not match. Say which fields, because `diagnosis` is
                        # what runner.py puts in the repair turn - telling the
                        # model "recovered" here would hand it back a success
                        # notice instead of the problem to fix.
                        if diagnosis is not None:
                            diagnosis.extend(
                                f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}"
                                for err in e.errors()[:4]
                            )
                    else:
                        # Only once it has actually validated.
                        if diagnosis is not None:
                            diagnosis.append("recovered by repairing mismatched delimiters")
                        return parsed
        return None

    async def ainvoke_structured(
        self,
        output_model: type[T],
        system_prompt: str = "",
        user_prompt: str = "",
    ) -> T:
        """Send a prompt and get back a validated Pydantic model.

        Single attempt with no repair. Agents go through
        ``app.analysis.runner.AgentRunner`` instead, which retries.
        """
        messages = self.build_json_messages(output_model, system_prompt, user_prompt)
        raw = await self.acomplete(messages)
        parsed = self.parse_json_response(raw.content, output_model)
        if parsed is not None:
            return parsed
        raise RuntimeError(f"Failed to parse {output_model.__name__} from model output")
