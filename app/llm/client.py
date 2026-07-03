"""LLM client wrapping ChatNVIDIA for all agent calls.

Follows the tutorial pattern from
nvidia_nims_prompt_interactive_tutorial/10.1_Appendix_Chaining Prompts.ipynb
"""

import json
import logging
from typing import TypeVar

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from pydantic import BaseModel, ValidationError

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    """Thin wrapper around ChatNVIDIA with support for structured outputs."""

    def __init__(self, model: str | None = None) -> None:
        self._model = model or settings.nvidia_model
        self._llm = ChatNVIDIA(
            model=self._model,
            temperature=settings.temperature,
            max_completion_tokens=settings.max_tokens,
            timeout=settings.llm_timeout,
            api_key=settings.nvidia_api_key or None,
        )

    def with_model(self, model_id: str) -> "LLMClient":
        """Return a new LLMClient using a different model. Original unchanged."""
        return LLMClient(model=model_id)

    def invoke(
        self,
        system_prompt: str = "",
        user_prompt: str = "",
        prefill: str = "",
    ) -> str:
        """Send a prompt with optional system prompt, AI prefill, and history.

        Matches the get_completion() pattern from the tutorial notebooks.
        """
        messages: list = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        if user_prompt:
            messages.append(HumanMessage(content=user_prompt))
        if prefill:
            messages.append(AIMessage(content=prefill))

        response = self._llm.invoke(messages)
        return response.content

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

    def _example_for_prop(self, prop: dict, defs: dict, depth: int = 0) -> str:
        if depth > 3:
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
                subs = [f'"{k}": {self._example_for_prop(v, defs, depth + 1)}' for k, v in nested.items()]
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
                subs = [f'"{k}": {self._example_for_prop(v, defs, depth + 1)}' for k, v in nested.items()]
                return "{" + ", ".join(subs) + "}"
            else:
                return "{}"
        else:
            title = prop.get("description", prop.get("title", ptype))
            return f'"<{title}>"'

    def _build_example_schema(self, model: type[T]) -> str:
        schema = model.model_json_schema()
        defs = schema.get("$defs", {})
        props = schema.get("properties", {})
        parts = [f'  "{k}": {self._example_for_prop(v, defs)}' for k, v in props.items()]
        return "{\n" + ",\n".join(parts) + "\n}"

    def _build_json_prompt(
        self, output_model: type[T], system_prompt: str, user_prompt: str,
    ) -> tuple[str, str]:
        example = self._build_example_schema(output_model)
        json_instruction = (
            f"\n\nRespond with ONLY valid JSON in this format (no markdown, no explanation):\n{example}"
        )
        return system_prompt + json_instruction, user_prompt

    def _parse_json_response(self, content: str, output_model: type[T]) -> T | None:
        text = content.strip()
        text = text.removeprefix("```json").removeprefix("```").strip()
        if text.endswith("```"):
            text = text[:-3].strip()
        # Find ALL JSON objects - model sometimes echoes the schema before outputting data
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
        # Try each object - take the last valid one (model may echo schema first)
        for candidate in reversed(objects):
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return output_model.model_validate(data)
            except (json.JSONDecodeError, ValidationError):
                continue
        return None

    def invoke_structured(
        self,
        output_model: type[T],
        system_prompt: str = "",
        user_prompt: str = "",
    ) -> T:
        """Send a prompt and get back a validated Pydantic model (sync)."""
        sys_prompt, usr_prompt = self._build_json_prompt(
            output_model, system_prompt, user_prompt,
        )
        messages: list = []
        if sys_prompt:
            messages.append(SystemMessage(content=sys_prompt))
        if usr_prompt:
            messages.append(HumanMessage(content=usr_prompt))

        response = self._llm.invoke(messages)
        content = str(response.content) if not isinstance(response.content, str) else response.content
        parsed = self._parse_json_response(content, output_model)
        if parsed is not None:
            return parsed

        raise RuntimeError(
            f"Failed to parse {output_model.__name__} from model output"
        )

    async def ainvoke_structured(
        self,
        output_model: type[T],
        system_prompt: str = "",
        user_prompt: str = "",
    ) -> T:
        """Send a prompt and get back a validated Pydantic model (async)."""
        sys_prompt, usr_prompt = self._build_json_prompt(
            output_model, system_prompt, user_prompt,
        )
        messages: list = []
        if sys_prompt:
            messages.append(SystemMessage(content=sys_prompt))
        if usr_prompt:
            messages.append(HumanMessage(content=usr_prompt))

        response = await self._llm.ainvoke(messages)
        content = str(response.content) if not isinstance(response.content, str) else response.content
        parsed = self._parse_json_response(content, output_model)
        if parsed is not None:
            return parsed

        raise RuntimeError(
            f"Failed to parse {output_model.__name__} from model output"
        )
