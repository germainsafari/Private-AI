import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Union

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from orchestrator import QueueFullError
from router import BackendConfig, ModelRouter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

_DEFAULT_BASE = "http://127.0.0.1:8080/v1"
OPENAI_BASE = (
    os.getenv("OPENAI_BASE_URL")
    or os.getenv("VLLM_BASE_URL")
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
_api = (os.getenv("VLLM_API_KEY") or os.getenv("API_KEY", "")).strip()
API_KEY = _api if _api else "not-needed"

REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "300"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "4096"))

# Admission control: how many requests may be in flight against the backend
# at once, and how many more may wait in the queue before we shed load.
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "8"))
MAX_QUEUE_DEPTH = int(os.getenv("MAX_QUEUE_DEPTH", "64"))
GPU_DEVICE_INDEX = int(os.getenv("GPU_DEVICE_INDEX", "0"))

# Optional second, smaller/faster backend for SLA-based routing and
# graceful degradation. Disabled unless FAST_BASE_URL is set.
FAST_BASE_URL = os.getenv("FAST_BASE_URL", "").strip()
FAST_MODEL_NAME = os.getenv("FAST_MODEL_NAME", "").strip()
FAST_MAX_CONCURRENCY = int(os.getenv("FAST_MAX_CONCURRENCY", "8"))
SLA_TTFT_MS = float(os.getenv("SLA_TTFT_MS", "1500"))

DEFAULT_SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a helpful, concise AI assistant with vision capabilities. "
    "You can analyze images, documents and charts in addition to text. "
    "Use Markdown formatting when it improves clarity.",
).strip()

_backend_configs = [
    BackendConfig(
        name="primary",
        base_url=OPENAI_BASE,
        model_name=MODEL_NAME,
        api_key=API_KEY,
        sla_ttft_ms=SLA_TTFT_MS,
        max_concurrency=MAX_CONCURRENCY,
        max_queue_depth=MAX_QUEUE_DEPTH,
        fallback="fast" if FAST_BASE_URL else None,
        request_timeout_s=REQUEST_TIMEOUT_SECONDS,
    )
]
if FAST_BASE_URL:
    _backend_configs.append(
        BackendConfig(
            name="fast",
            base_url=FAST_BASE_URL,
            model_name=FAST_MODEL_NAME or "fast-model",
            api_key=API_KEY,
            sla_ttft_ms=SLA_TTFT_MS * 2,  # the fallback itself has no further fallback
            max_concurrency=FAST_MAX_CONCURRENCY,
            max_queue_depth=MAX_QUEUE_DEPTH,
            fallback=None,
            request_timeout_s=REQUEST_TIMEOUT_SECONDS,
        )
    )

router = ModelRouter(_backend_configs, gpu_device_index=GPU_DEVICE_INDEX)
primary_backend = router.backends[router.default_name]

app = FastAPI(title="Inference Engineering Platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ImageContent(BaseModel):
    type: str = "image_url"
    image_url: dict[str, str]


class TextContent(BaseModel):
    type: str = "text"
    text: str


class ChatMessage(BaseModel):
    role: str = Field(..., pattern=r"^(system|user|assistant)$")
    content: Union[str, list[Union[TextContent, ImageContent, dict[str, Any]]]]


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    system: Optional[str] = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1, le=MAX_OUTPUT_TOKENS)
    stream: bool = True
    # Optional logical backend name (e.g. "primary" or "fast") for multi-model
    # SLA routing. Omit to use the default backend with automatic degradation.
    model: Optional[str] = None


def _build_messages(body: ChatRequest) -> list[dict[str, Any]]:
    system_content = (body.system or DEFAULT_SYSTEM_PROMPT).strip()
    msgs: list[dict[str, Any]] = []
    if system_content:
        msgs.append({"role": "system", "content": system_content})

    for m in body.messages:
        if m.role == "system":
            continue

        if isinstance(m.content, str):
            msgs.append({"role": m.role, "content": m.content})
        elif isinstance(m.content, list):
            content_list = []
            for item in m.content:
                if isinstance(item, dict):
                    content_list.append(item)
                elif hasattr(item, "model_dump"):
                    content_list.append(item.model_dump())
                else:
                    content_list.append(item)
            msgs.append({"role": m.role, "content": content_list})
        else:
            msgs.append({"role": m.role, "content": str(m.content)})

    return msgs


@app.post("/api/chat")
async def chat(body: ChatRequest) -> Any:
    messages = _build_messages(body)

    has_images = any(
        isinstance(m.get("content"), list)
        and any(c.get("type") == "image_url" for c in m["content"] if isinstance(c, dict))
        for m in messages
    )

    backend, degraded = router.resolve(body.model)

    try:
        ticket_cm = backend.orchestrator.track()
        ticket = await ticket_cm.__aenter__()
    except QueueFullError as e:
        backend.metrics.record_rejection()
        raise HTTPException(status_code=429, detail=str(e))

    logger.info(
        "chat request admitted: backend=%s degraded=%s msgs=%d stream=%s images=%s model=%s wait_ms=%.1f",
        backend.config.name, degraded, len(messages), body.stream, has_images,
        backend.config.model_name, ticket.wait_ms or 0.0,
    )

    if not body.stream:
        try:
            completion = await backend.client.chat.completions.create(
                model=backend.config.model_name,
                messages=messages,
                temperature=body.temperature,
                max_tokens=body.max_tokens,
            )
            content = completion.choices[0].message.content or ""
            usage = getattr(completion, "usage", None)
            output_tokens = getattr(usage, "completion_tokens", 0) or 0
            ticket.first_token_at = time.perf_counter()
            ticket.output_tokens = output_tokens
            await ticket_cm.__aexit__(None, None, None)
            backend.metrics.record_completion(
                wait_ms=ticket.wait_ms,
                ttft_ms=ticket.ttft_ms,
                processing_ms=ticket.processing_ms,
                total_ms=ticket.total_ms,
                output_tokens=output_tokens,
            )
            return {
                "content": content,
                "model": backend.config.model_name,
                "backend": backend.config.name,
                "degraded": degraded,
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Non-streaming completion failed")
            await ticket_cm.__aexit__(type(e), e, e.__traceback__)
            backend.metrics.record_completion(
                wait_ms=ticket.wait_ms, ttft_ms=None, processing_ms=None,
                total_ms=ticket.total_ms, output_tokens=0, error=True,
            )
            raise HTTPException(status_code=502, detail=f"Model error: {e}")

    async def event_stream() -> AsyncIterator[str]:
        output_tokens = 0
        exc: Optional[BaseException] = None
        try:
            stream = await backend.client.chat.completions.create(
                model=backend.config.model_name,
                messages=messages,
                temperature=body.temperature,
                max_tokens=body.max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
            async for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if usage is not None and getattr(usage, "completion_tokens", None):
                    output_tokens = usage.completion_tokens
                if not chunk.choices:
                    continue
                try:
                    delta = chunk.choices[0].delta
                    piece = getattr(delta, "content", None) or ""
                except Exception:
                    piece = ""
                if piece:
                    if ticket.first_token_at is None:
                        ticket.first_token_at = time.perf_counter()
                    if not output_tokens:
                        output_tokens += 1
                    yield f"data: {json.dumps({'delta': piece})}\n\n"
            yield f"data: {json.dumps({'done': True, 'ttft_ms': ticket.ttft_ms, 'backend': backend.config.name, 'degraded': degraded})}\n\n"
        except asyncio.CancelledError:
            exc = asyncio.CancelledError()
            raise
        except Exception as e:
            exc = e
            logger.exception("Streaming completion failed")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            ticket.output_tokens = output_tokens
            await ticket_cm.__aexit__(
                type(exc) if exc else None, exc, exc.__traceback__ if exc else None
            )
            backend.metrics.record_completion(
                wait_ms=ticket.wait_ms,
                ttft_ms=ticket.ttft_ms,
                processing_ms=ticket.processing_ms,
                total_ms=ticket.total_ms,
                output_tokens=output_tokens,
                error=exc is not None,
            )

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
async def list_models() -> dict[str, Any]:
    try:
        models = await primary_backend.client.models.list()
        data = [{"id": m.id} for m in models.data]
    except Exception as e:
        logger.warning(f"Could not list models: {e}")
        data = [{"id": MODEL_NAME}]
    return {"current": MODEL_NAME, "models": data, "backends": list(router.backends.keys())}


@app.get("/api/health")
async def health() -> dict[str, Any]:
    # Never fail the process on upstream unavailability — Vercel / cold starts
    # should still get a 200 so the deployment health check stays green.
    backend_ok = False
    try:
        await primary_backend.client.models.list()
        backend_ok = True
    except Exception as e:
        logger.warning("health: upstream unreachable: %s", e)

    return {
        "status": "ok" if backend_ok else "degraded",
        "model": MODEL_NAME,
        "endpoint": OPENAI_BASE,
        "backend_reachable": backend_ok,
        "vision": True,
        "multi_model_routing": len(router.backends) > 1,
    }


@app.get("/api/metrics")
async def get_metrics() -> JSONResponse:
    return JSONResponse(primary_backend.metrics.snapshot(orchestrator=primary_backend.orchestrator))


@app.get("/api/metrics/stream")
async def stream_metrics(request: Request) -> StreamingResponse:
    async def gen() -> AsyncIterator[str]:
        while True:
            if await request.is_disconnected():
                break
            payload = primary_backend.metrics.snapshot(orchestrator=primary_backend.orchestrator)
            yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/router/status")
async def router_status() -> JSONResponse:
    return JSONResponse(router.snapshot())


if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/")
def root() -> FileResponse:
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=500, detail="Missing static/index.html")
    return FileResponse(index)


@app.get("/dashboard")
def dashboard() -> FileResponse:
    page = STATIC_DIR / "dashboard.html"
    if not page.is_file():
        raise HTTPException(status_code=500, detail="Missing static/dashboard.html")
    return FileResponse(page)
