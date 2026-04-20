import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

_DEFAULT_BASE = "http://localhost:12434/engines/v1"
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
    or "local-model"
).strip()
_api = os.getenv("API_KEY", "").strip()
API_KEY = _api if _api else "not-needed"

DEFAULT_SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are Admind, a helpful, concise, and thoughtful AI assistant. "
    "Answer clearly and use Markdown (including code blocks with language tags) when useful.",
).strip()

client = OpenAI(base_url=OPENAI_BASE, api_key=API_KEY)

app = FastAPI(title="Admind Chat")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatMessage(BaseModel):
    role: str = Field(..., pattern=r"^(system|user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    system: Optional[str] = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=1, le=8192)
    stream: bool = True


def _build_messages(body: ChatRequest) -> list[dict[str, str]]:
    system_content = (body.system or DEFAULT_SYSTEM_PROMPT).strip()
    msgs: list[dict[str, str]] = []
    if system_content:
        msgs.append({"role": "system", "content": system_content})
    for m in body.messages:
        if m.role == "system":
            continue
        msgs.append({"role": m.role, "content": m.content})
    return msgs


@app.post("/api/chat")
def chat(body: ChatRequest) -> Any:
    messages = _build_messages(body)
    logger.info(
        f"Chat request: {len(messages)} msgs, stream={body.stream}, "
        f"model={MODEL_NAME} @ {OPENAI_BASE}"
    )

    if not body.stream:
        try:
            completion = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=body.temperature,
                max_tokens=body.max_tokens,
            )
            content = completion.choices[0].message.content or ""
            return {"content": content, "model": MODEL_NAME}
        except Exception as e:
            logger.exception("Non-streaming completion failed")
            raise HTTPException(status_code=502, detail=f"Model error: {e}")

    def event_stream():
        try:
            stream = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=body.temperature,
                max_tokens=body.max_tokens,
                stream=True,
            )
            for chunk in stream:
                try:
                    delta = chunk.choices[0].delta
                    piece = getattr(delta, "content", None) or ""
                except Exception:
                    piece = ""
                if piece:
                    yield f"data: {json.dumps({'delta': piece})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            logger.exception("Streaming completion failed")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/models")
def list_models() -> dict[str, Any]:
    try:
        models = client.models.list()
        data = [{"id": m.id} for m in models.data]
    except Exception as e:
        logger.warning(f"Could not list models: {e}")
        data = [{"id": MODEL_NAME}]
    return {"current": MODEL_NAME, "models": data}


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "endpoint": OPENAI_BASE,
    }


if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/")
def root() -> FileResponse:
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=500, detail="Missing static/index.html")
    return FileResponse(index)
