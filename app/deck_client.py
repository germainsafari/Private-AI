"""Client for the Admind deck engine (Ppttx maker) so presentations keep the full brand template.

The gateway creates and polls jobs server-side; the browser downloads the .pptx and previews
straight from the engine with short-lived signed links, so large decks never pass through Vercel.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

import httpx

DECK_ENGINE_URL = os.getenv("DECK_ENGINE_URL", "").strip().rstrip("/")
DECK_ENGINE_TOKEN_SECRET = os.getenv("DECK_ENGINE_TOKEN_SECRET", "").strip()
DECK_ENGINE_API_KEY = os.getenv("DECK_ENGINE_API_KEY", "").strip()
DECK_LINK_TTL_SECONDS = int(os.getenv("DECK_LINK_TTL_SECONDS", "7200"))


class DeckError(RuntimeError):
    pass


def deck_available() -> bool:
    return bool(DECK_ENGINE_URL)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def mint_token(ttl: int = DECK_LINK_TTL_SECONDS) -> str:
    """Same token format as engine/app/auth.py in the deck engine."""
    if not DECK_ENGINE_TOKEN_SECRET:
        return ""
    payload = _b64(json.dumps({"sub": "private-ai", "exp": int(time.time()) + ttl}).encode())
    sig = _b64(hmac.new(DECK_ENGINE_TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def _headers() -> dict[str, str]:
    if DECK_ENGINE_API_KEY:
        return {"X-API-Key": DECK_ENGINE_API_KEY}
    token = mint_token(600)
    return {"Authorization": f"Bearer {token}"} if token else {}


async def create_job(
    prompt: str,
    *,
    slide_count: int,
    language: str,
    files: list[tuple[str, bytes, str]],
) -> str:
    if not deck_available():
        raise DeckError("Presentations need DECK_ENGINE_URL (the Admind deck engine).")
    data = {"prompt": prompt, "slide_count": str(slide_count), "language": language or "auto"}
    multipart = [("files", (name, content, mime)) for name, content, mime in files]
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{DECK_ENGINE_URL}/api/jobs", data=data, files=multipart or None, headers=_headers())
    if r.status_code != 200:
        raise DeckError(f"Deck engine HTTP {r.status_code}: {r.text[:300]}")
    return r.json()["id"]


async def job_status(job_id: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{DECK_ENGINE_URL}/api/jobs/{job_id}", headers=_headers())
    if r.status_code != 200:
        raise DeckError(f"Deck engine HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def public_links(job_id: str, previews: int, revision: int = 0) -> dict:
    token = mint_token()
    q = f"?token={token}" if token else ""
    sep = "&" if q else "?"
    return {
        "download_url": f"{DECK_ENGINE_URL}/api/jobs/{job_id}/deck.pptx{q}",
        "previews": [
            f"{DECK_ENGINE_URL}/api/jobs/{job_id}/previews/{n}.png{q}{sep}r={revision}"
            for n in range(1, previews + 1)
        ],
        "expires_in": DECK_LINK_TTL_SECONDS,
    }
