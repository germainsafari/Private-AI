"""Turn ingest output into plain text the chat model can always accept.

Two vLLM limits make raw ingest output unsafe to send as-is:
  * at most VLLM_MAX_IMAGES_PER_PROMPT images per request, and
  * a finite context window (max_model_len).
Images are therefore transcribed to text in batches, and text that does not fit
the context window is condensed part by part instead of being cut off.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from ingest import VLLM_MAX_IMAGES_PER_PROMPT, VisionImage

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = float(os.getenv("CHARS_PER_TOKEN", "2.5"))
CONDENSE_CHUNK_CHARS = int(os.getenv("CONDENSE_CHUNK_CHARS", "40000"))
CONDENSE_MAX_TOKENS = int(os.getenv("CONDENSE_MAX_TOKENS", "4096"))
CONDENSE_MAX_ROUNDS = int(os.getenv("CONDENSE_MAX_ROUNDS", "4"))
VISION_MAX_TOKENS = int(os.getenv("VISION_MAX_TOKENS", "2048"))
DIGEST_CONCURRENCY = int(os.getenv("DIGEST_CONCURRENCY", "4"))

VISION_PROMPT = (
    "You are transcribing figures from {label} so that a reader who cannot see "
    "them gets all of their content. The images are, in order:\n{listing}\n\n"
    "For each image write a section headed 'Figure N - <source>' using the list "
    "above, containing: all visible text verbatim; any table as a Markdown table; "
    "for charts the chart type, axes, series and every readable value; and one "
    "sentence describing anything else shown. Do not invent content. If an image "
    "is blank, a logo or purely decorative, say so in one line."
)

CONDENSE_PROMPT = (
    "Below is part {i} of {n} of one or more documents a user attached to a chat. "
    "Rewrite it as dense, well-organised Markdown reference notes that keep EVERY "
    "fact: names, dates, numbers, amounts, percentages, table rows, definitions, "
    "requirements, decisions and important quotes. Keep the heading structure and "
    "the '=== FILE: ... ===' markers. Remove only repetition and filler. Do not add "
    "anything that is not in the text.\n\n{chunk}"
)


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def _batches(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _split_text(text: str, size: int) -> list[str]:
    """Split on paragraph boundaries into chunks of at most ~size chars."""
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for para in text.split("\n\n"):
        while len(para) > size:
            if current:
                chunks.append("\n\n".join(current))
                current, length = [], 0
            chunks.append(para[:size])
            para = para[size:]
        if length + len(para) > size and current:
            chunks.append("\n\n".join(current))
            current, length = [], 0
        current.append(para)
        length += len(para) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


async def _complete(client: Any, model: str, extra: dict[str, Any], content: Any, max_tokens: int) -> str:
    resp = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        temperature=0.0,
        max_tokens=max_tokens,
        **extra,
    )
    msg = resp.choices[0].message
    return (getattr(msg, "content", None) or getattr(msg, "reasoning", None) or "").strip()


async def transcribe_images(
    client: Any,
    model: str,
    extra: dict[str, Any],
    images: list[VisionImage],
    label: str,
) -> tuple[str, list[str]]:
    """Describe every image as text, VLLM_MAX_IMAGES_PER_PROMPT images per request."""
    if not images:
        return "", []
    sem = asyncio.Semaphore(DIGEST_CONCURRENCY)
    warnings: list[str] = []
    batches = _batches(list(enumerate(images, start=1)), VLLM_MAX_IMAGES_PER_PROMPT)

    async def run(batch: list[tuple[int, VisionImage]]) -> str:
        listing = "\n".join(f"- Figure {n}: {img.source}" for n, img in batch)
        content: list[dict[str, Any]] = [
            {"type": "text", "text": VISION_PROMPT.format(label=label, listing=listing)}
        ]
        content += [
            {"type": "image_url", "image_url": {"url": img.to_data_url()}} for _, img in batch
        ]
        async with sem:
            try:
                return await _complete(client, model, extra, content, VISION_MAX_TOKENS)
            except Exception as e:
                logger.warning("vision transcription failed for %s: %s", listing, e)
                first, last = batch[0][0], batch[-1][0]
                warnings.append(f"{label}: figures {first}-{last} could not be transcribed ({e})")
                return "\n".join(f"Figure {n} ({img.source}): [not transcribed]" for n, img in batch)

    parts = await asyncio.gather(*(run(b) for b in batches))
    return "\n\n".join(p for p in parts if p), warnings


async def fit_text(
    client: Any,
    model: str,
    extra: dict[str, Any],
    text: str,
    budget_chars: int,
) -> tuple[str, list[str]]:
    """Condense text part by part until it fits budget_chars; never drops it silently."""
    warnings: list[str] = []
    original = len(text)
    rounds = 0
    sem = asyncio.Semaphore(DIGEST_CONCURRENCY)

    while len(text) > budget_chars and rounds < CONDENSE_MAX_ROUNDS:
        rounds += 1
        chunks = _split_text(text, CONDENSE_CHUNK_CHARS)

        async def run(i: int, chunk: str) -> str:
            prompt = CONDENSE_PROMPT.format(i=i, n=len(chunks), chunk=chunk)
            async with sem:
                try:
                    notes = await _complete(client, model, extra, prompt, CONDENSE_MAX_TOKENS)
                    return notes or chunk
                except Exception as e:
                    logger.warning("condense failed for part %d: %s", i, e)
                    warnings.append(f"part {i} could not be condensed ({e}); kept as-is")
                    return chunk

        notes = await asyncio.gather(*(run(i, c) for i, c in enumerate(chunks, start=1)))
        condensed = "\n\n".join(notes)
        if len(condensed) >= len(text):
            break
        text = condensed

    if rounds:
        warnings.append(
            f"documents were {original:,} chars, too long for the model context; "
            f"condensed to {len(text):,} chars of notes in {rounds} pass(es)"
        )
    if len(text) > budget_chars:
        warnings.append(
            f"notes still exceed the context budget; last {len(text) - budget_chars:,} chars omitted"
        )
        text = text[:budget_chars] + "\n\n[… remaining notes omitted to fit the model context …]"
    return text, warnings
