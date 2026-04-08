import json
import os
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel, Field

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Default base uses the gemma4.cpp engine path (see Docker DMR docs: optional /engines/<engine>/v1).
# Compose "models" binding can set OPENAI_BASE_URL / MODEL_NAME, or short syntax: LLM_URL / LLM_MODEL.
_DEFAULT_BASE = "http://localhost:12434/engines/gemma4.cpp/v1"
OPENAI_BASE = (
    os.getenv("OPENAI_BASE_URL")
    or os.getenv("BASE_URL")
    or os.getenv("LLM_URL")
    or _DEFAULT_BASE
).strip()
MODEL_NAME = (
    os.getenv("MODEL_NAME")
    or os.getenv("MODEL")
    or os.getenv("LLM_MODEL")
    or "ai/gemma4:latest"
).strip()
# DMR does not validate keys, but the OpenAI Python SDK rejects an empty api_key.
_api = os.getenv("API_KEY", "").strip()
API_KEY = _api if _api else "dmr-local"

client = OpenAI(base_url=OPENAI_BASE, api_key=API_KEY)

app = FastAPI(title="Polish Practice")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# lesson_id -> { "answer": [...], "expires": epoch }
_lessons: dict[str, dict[str, Any]] = {}
_LESSON_TTL = 3600


def _cleanup_lessons() -> None:
    now = time.time()
    dead = [k for k, v in _lessons.items() if v.get("expires", 0) < now]
    for k in dead:
        del _lessons[k]


def _parse_json_content(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _fallback_lesson(topic: str) -> dict[str, Any]:
    """Offline exercise when the model is unreachable."""
    bank = ["Rano", "Anna", "idzie", "do", "sklepu."]
    random.shuffle(bank)
    return {
        "title": f"{topic} (offline)",
        "context_en": "Anna goes to the shop in the morning.",
        "fragments": [
            {"type": "text", "value": ""},
            {"type": "blank", "id": 0},
            {"type": "text", "value": " "},
            {"type": "blank", "id": 1},
            {"type": "text", "value": " "},
            {"type": "blank", "id": 2},
            {"type": "text", "value": " "},
            {"type": "blank", "id": 3},
            {"type": "text", "value": " "},
            {"type": "blank", "id": 4},
        ],
        "word_bank": bank,
        "answer": ["Rano", "Anna", "idzie", "do", "sklepu."],
    }


def _build_prompt(topic: str) -> str:
    return f"""Create ONE Polish language exercise for the lesson topic: "{topic}".

Rules:
- Write 1–2 short Polish sentences (total 6–14 words) suitable for beginners.
- Replace exactly 4–6 consecutive words with blanks for a drag-and-drop exercise. Each blank is ONE word (keep punctuation attached to the word token if natural, e.g. "sklepu.").
- The learner will see an English hint in context_en.

Return ONLY valid JSON (no markdown) with this shape:
{{
  "title": "short Polish title",
  "context_en": "one sentence English summary",
  "fragments": [
    {{"type": "text", "value": "Polish text before first gap "}},
    {{"type": "blank", "id": 0}},
    {{"type": "text", "value": " optional space or punctuation between gaps "}},
    ...
  ],
  "answer": ["word0", "word1", ...]
}}

Requirements:
- fragments must alternate text and blank tokens; first and last can be text (possibly empty string).
- blank ids are 0..n-1 in order left-to-right.
- "answer" array order matches blank id order (answer[0] fills blank id 0).
- Use only Polish in fragments and answer tokens."""


class LessonRequest(BaseModel):
    topic: str = Field(default="Greetings", min_length=1, max_length=80)


class CheckRequest(BaseModel):
    lesson_id: str
    words: list[str] = Field(default_factory=list)


@app.post("/api/lesson")
def create_lesson(body: LessonRequest) -> dict[str, Any]:
    _cleanup_lessons()
    topic = body.topic.strip()
    payload: dict[str, Any]

    data: Optional[dict[str, Any]] = None
    try:
        kwargs: dict[str, Any] = {
            "model": MODEL_NAME,
            "messages": [
                {
                    "role": "system",
                    "content": "You write compact JSON only. You teach Polish. Never add commentary outside JSON.",
                },
                {"role": "user", "content": _build_prompt(topic)},
            ],
            "temperature": 0.7,
            "max_tokens": 900,
        }
        try:
            completion = client.chat.completions.create(
                **kwargs, response_format={"type": "json_object"}
            )
        except Exception:
            completion = client.chat.completions.create(**kwargs)
        raw = completion.choices[0].message.content or "{}"
        data = _parse_json_content(raw)
    except Exception:
        data = None

    if data is None:
        data = _fallback_lesson(topic)

    answer = data.get("answer") or []
    fragments = data.get("fragments") or []
    if not isinstance(answer, list) or not answer:
        data = _fallback_lesson(topic)
        answer = data["answer"]
        fragments = data["fragments"]

    bank = list(answer)
    random.shuffle(bank)

    lesson_id = str(uuid.uuid4())
    _lessons[lesson_id] = {
        "answer": [str(w) for w in answer],
        "expires": time.time() + _LESSON_TTL,
    }

    return {
        "lesson_id": lesson_id,
        "title": data.get("title", topic),
        "context_en": data.get("context_en", ""),
        "fragments": fragments,
        "word_bank": bank,
    }


@app.post("/api/check")
def check_lesson(body: CheckRequest) -> dict[str, Any]:
    _cleanup_lessons()
    entry = _lessons.get(body.lesson_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Lesson expired or unknown.")

    expected = entry["answer"]
    got = [str(w).strip() for w in body.words]
    ok = len(got) == len(expected) and all(
        a == b for a, b in zip(expected, got)
    )
    if ok:
        del _lessons[body.lesson_id]

    return {"correct": ok, "expected_len": len(expected)}


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "model": MODEL_NAME}


if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/")
def root() -> FileResponse:
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=500, detail="Missing static/index.html")
    return FileResponse(index)
