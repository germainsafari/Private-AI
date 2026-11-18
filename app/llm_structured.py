"""JSON-schema constrained calls to the vLLM backend (same approach as the deck engine)."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

DISABLE_THINKING = os.getenv("LLM_DISABLE_THINKING", "1").strip() not in ("0", "false", "no")


class StructuredError(RuntimeError):
    pass


def _strip(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^<think>.*?</think>", "", text, flags=re.S).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return text


async def structured(
    client: AsyncOpenAI,
    model: str,
    messages: list[dict],
    schema: type[T],
    *,
    max_tokens: int = 2048,
    temperature: float = 0.4,
    retries: int = 2,
) -> T:
    extra = {"chat_template_kwargs": {"enable_thinking": False}} if DISABLE_THINKING else None
    last: Exception | None = None
    for attempt in range(retries + 1):
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature if attempt == 0 else 0.2,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": True,
                },
            },
            extra_body=extra,
        )
        text = resp.choices[0].message.content or ""
        try:
            return schema.model_validate_json(_strip(text))
        except (ValidationError, json.JSONDecodeError) as e:
            last = e
            logger.warning("%s invalid (attempt %d): %s", schema.__name__, attempt + 1, str(e)[:300])
            messages = messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": f"That JSON was invalid: {str(e)[:400]}. Return corrected JSON only."},
            ]
    raise StructuredError(f"Could not get valid {schema.__name__}: {last}")
