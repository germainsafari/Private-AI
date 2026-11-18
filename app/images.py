"""Optional image generation through any OpenAI-compatible /images/generations endpoint.

Disabled unless IMAGE_BASE_URL is set. Prompts are sent to that provider, so point it at a
self-hosted server if prompts must stay on your own hardware.
"""

from __future__ import annotations

import base64
import io
import os

import httpx

from brand import IMAGE_STYLE_HINT

IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL", "").strip().rstrip("/")
IMAGE_API_KEY = os.getenv("IMAGE_API_KEY", "").strip()
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-1").strip()
IMAGE_SIZE = os.getenv("IMAGE_SIZE", "1536x1024").strip()
IMAGE_TIMEOUT_SECONDS = float(os.getenv("IMAGE_TIMEOUT_SECONDS", "180"))
MAX_REPORT_IMAGES = int(os.getenv("MAX_REPORT_IMAGES", "4"))


class ImageError(RuntimeError):
    pass


def images_available() -> bool:
    return bool(IMAGE_BASE_URL)


def _to_jpeg(data: bytes, quality: int = 85) -> bytes:
    """Keep generated files small enough for Vercel's response limit."""
    from PIL import Image

    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((1600, 1600))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()


async def generate_image(prompt: str, *, brand_style: bool = True, size: str | None = None) -> bytes:
    if not images_available():
        raise ImageError("Image generation is not configured (set IMAGE_BASE_URL).")
    full_prompt = f"{prompt.strip()}\n\nStyle: {IMAGE_STYLE_HINT}" if brand_style else prompt.strip()
    headers = {"Authorization": f"Bearer {IMAGE_API_KEY}"} if IMAGE_API_KEY else {}
    body = {"model": IMAGE_MODEL, "prompt": full_prompt[:3800], "size": size or IMAGE_SIZE, "n": 1}
    async with httpx.AsyncClient(timeout=IMAGE_TIMEOUT_SECONDS) as client:
        r = await client.post(f"{IMAGE_BASE_URL}/images/generations", json=body, headers=headers)
        if r.status_code != 200:
            raise ImageError(f"Image provider HTTP {r.status_code}: {r.text[:300]}")
        item = (r.json().get("data") or [{}])[0]
        if item.get("b64_json"):
            raw = base64.b64decode(item["b64_json"])
        elif item.get("url"):
            img = await client.get(item["url"])
            img.raise_for_status()
            raw = img.content
        else:
            raise ImageError("Image provider returned no image")
    return _to_jpeg(raw)
