import asyncio
import copy
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Union

# Ensure sibling modules (orchestrator, router, metrics) are importable even
# when the entrypoint is executed from a different working directory, e.g. on
# Vercel's Python runtime where the function runs from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
load_dotenv()

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from digest import estimate_tokens, fit_text, transcribe_images
from ingest import (
    IngestError,
    VLLM_MAX_IMAGES_PER_PROMPT,
    combine_results,
    ingest_available,
    ingest_capabilities,
    parse_bytes,
)
import deck_client
from creator import CreateRequest, Creator, UploadedFile
from file_edit import EDITABLE_EXTENSIONS
from images import images_available
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

# Qwen3.x on vLLM may return empty `content` and fill `reasoning` when thinking is on.
LLM_DISABLE_THINKING = os.getenv("LLM_DISABLE_THINKING", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)


def _llm_extra_kwargs() -> dict[str, Any]:
    if LLM_DISABLE_THINKING:
        return {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    return {}


def _assistant_text(message: Any) -> str:
    content = getattr(message, "content", None) or ""
    if content:
        return content
    return getattr(message, "reasoning", None) or ""


def _delta_text(delta: Any) -> str:
    piece = getattr(delta, "content", None) or ""
    if piece:
        return piece
    return getattr(delta, "reasoning", None) or ""

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


MODEL_CONTEXT_TOKENS = int(os.getenv("MODEL_CONTEXT_TOKENS", "0"))
IMAGE_TOKEN_ESTIMATE = int(os.getenv("IMAGE_TOKEN_ESTIMATE", "3000"))
PROMPT_RESERVE_TOKENS = int(os.getenv("PROMPT_RESERVE_TOKENS", "6000"))
_context_cache: dict[str, int] = {}


async def _context_tokens(backend: Any) -> int:
    """Model context window: env override, else vLLM's max_model_len, else 32k."""
    if MODEL_CONTEXT_TOKENS > 0:
        return MODEL_CONTEXT_TOKENS
    name = backend.config.name
    if name not in _context_cache:
        size = 32768
        try:
            models = await backend.client.models.list()
            for m in models.data:
                if m.id == backend.config.model_name:
                    size = int(getattr(m, "max_model_len", None) or size)
                    break
        except Exception as e:
            logger.warning("could not read max_model_len: %s", e)
            return size
        _context_cache[name] = size
    return _context_cache[name]


def _message_tokens(msg: dict[str, Any]) -> int:
    content = msg.get("content")
    if isinstance(content, str):
        return estimate_tokens(content) + 8
    total = 8
    for part in content or []:
        if isinstance(part, dict) and part.get("type") == "image_url":
            total += IMAGE_TOKEN_ESTIMATE
        elif isinstance(part, dict):
            total += estimate_tokens(str(part.get("text", "")))
    return total


def _cap_multimodal_images(
    messages: list[dict[str, Any]], max_images: int
) -> tuple[list[dict[str, Any]], int]:
    """Keep the newest max_images image_url parts; older ones become a text note."""
    refs: list[tuple[int, int]] = []
    for mi, msg in enumerate(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for pi, part in enumerate(content):
            if isinstance(part, dict) and part.get("type") == "image_url":
                refs.append((mi, pi))
    excess = len(refs) - max(0, max_images)
    if excess <= 0:
        return messages, 0
    out = copy.deepcopy(messages)
    for mi, pi in refs[:excess]:
        out[mi]["content"][pi] = {"type": "text", "text": "[earlier image omitted]"}
    return out, excess


_DOC_MARKER = "\n\n--- Attached documents ---"


def _fit_messages(
    messages: list[dict[str, Any]], budget_tokens: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """Shrink conversation history until it fits; the newest user turn is kept whole."""
    notes: list[str] = []
    msgs = copy.deepcopy(messages)

    def total() -> int:
        return sum(_message_tokens(m) for m in msgs)

    if total() <= budget_tokens:
        return msgs, notes

    first = 1 if msgs and msgs[0]["role"] == "system" else 0
    last = len(msgs) - 1

    # 1. Drop document dumps from older turns (the model already answered about them).
    for i in range(first, last):
        if total() <= budget_tokens:
            break
        content = msgs[i].get("content")
        parts = [content] if isinstance(content, str) else [
            p for p in content or [] if isinstance(p, dict) and p.get("type") == "text"
        ]
        for idx, part in enumerate(parts):
            text = part if isinstance(part, str) else part.get("text", "")
            cut = text.find(_DOC_MARKER)
            if cut < 0:
                continue
            short = text[:cut] + "\n\n[documents attached earlier in this chat; omitted to fit context]"
            if isinstance(content, str):
                msgs[i]["content"] = short
            else:
                part["text"] = short
            notes.append("older attached documents omitted from history to fit context")

    # 2. Drop the oldest turns.
    while total() > budget_tokens and last - first > 0:
        msgs.pop(first)
        last -= 1
        notes.append("oldest messages dropped to fit context")

    return msgs, sorted(set(notes))


def _truncate_text_parts(msg: dict[str, Any], cut_chars: int) -> None:
    note = "\n\n[… truncated to fit the model context …]"
    content = msg.get("content")
    if isinstance(content, str):
        msg["content"] = content[: max(0, len(content) - cut_chars)] + note
        return
    texts = [p for p in content or [] if isinstance(p, dict) and p.get("type") == "text"]
    if texts:
        longest = max(texts, key=lambda p: len(p.get("text", "")))
        text = longest.get("text", "")
        longest["text"] = text[: max(0, len(text) - cut_chars)] + note


def _llm_for_ingest() -> tuple[Any, str, dict[str, Any]]:
    backend = primary_backend
    return backend.client, backend.config.model_name, _llm_extra_kwargs()


@app.post("/api/ingest")
async def ingest_files(files: list[UploadFile] = File(...)) -> JSONResponse:
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    results = []
    skipped: list[str] = []
    try:
        for upload in files:
            name = (upload.filename or "upload").strip() or "upload"
            data = await upload.read()
            try:
                results.append(await asyncio.to_thread(parse_bytes, name, data))
            except IngestError as e:
                skipped.append(str(e))
            except Exception as e:
                logger.exception("ingest failed for %s", name)
                skipped.append(f"{name}: could not be read ({e})")
    finally:
        for upload in files:
            try:
                await upload.close()
            except Exception:
                pass

    if not results:
        raise HTTPException(status_code=400, detail="; ".join(skipped) or "No readable files")

    client, model, extra = _llm_for_ingest()
    for r in results:
        if not r.images:
            continue
        figures, notes = await transcribe_images(client, model, extra, r.images, r.name)
        r.warnings.extend(notes)
        r.text = (r.text + "\n\n" if r.text.strip() else "") + (
            "[figures and scanned pages, transcribed by the vision model]\n" + figures
        )
        r.images = []

    batch = combine_results(results)
    ctx = await _context_tokens(primary_backend)
    budget_tokens = max(2000, ctx - MAX_OUTPUT_TOKENS - PROMPT_RESERVE_TOKENS)
    budget_chars = int(budget_tokens * float(os.getenv("CHARS_PER_TOKEN", "2.5")))
    batch.combined_text, fit_notes = await fit_text(
        client, model, extra, batch.combined_text, budget_chars
    )
    batch.warnings = skipped + batch.warnings + fit_notes

    payload = batch.to_api_dict()
    payload.update(ingest_capabilities())
    return JSONResponse(payload)


@app.get("/api/ingest/formats")
async def ingest_formats() -> dict[str, Any]:
    return ingest_capabilities()


_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
MAX_CREATE_UPLOAD_BYTES = int(os.getenv("MAX_CREATE_UPLOAD_BYTES", str(4 * 1024 * 1024)))


@app.post("/api/create")
async def create_file(
    prompt: str = Form(...),
    action: str = Form("auto"),
    context: str = Form(""),
    history: str = Form(""),
    previous_markdown: str = Form(""),
    previous_kind: str = Form(""),
    language: str = Form(""),
    files: list[UploadFile] = File(default=[]),
) -> StreamingResponse:
    uploads: list[UploadedFile] = []
    for upload in files or []:
        data = await upload.read()
        await upload.close()
        if len(data) > MAX_CREATE_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"{upload.filename} is too large to edit here")
        name = (upload.filename or "upload").strip() or "upload"
        text = ""
        try:
            text = (await asyncio.to_thread(parse_bytes, name, data)).text
        except Exception as e:
            logger.info("create: could not extract text from %s: %s", name, e)
        uploads.append(UploadedFile(name, data, upload.content_type or "application/octet-stream", text))

    req = CreateRequest(
        prompt=prompt.strip(),
        action=action.strip().lower(),
        context=context,
        history=history,
        previous_markdown=previous_markdown,
        previous_kind=previous_kind,
        language=language,
        files=uploads,
    )
    creator = Creator(primary_backend.client, primary_backend.config.model_name)

    async def events() -> AsyncIterator[str]:
        queue: asyncio.Queue[Optional[dict]] = asyncio.Queue()

        async def pump() -> None:
            try:
                async for ev in creator.run(req):
                    await queue.put(ev)
            except Exception as e:
                logger.exception("create failed")
                await queue.put({"type": "error", "message": f"Could not create the file: {e}"})
            finally:
                await queue.put(None)

        task = asyncio.create_task(pump())
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if ev is None:
                    break
                if ev.get("type") == "keepalive":
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(ev)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        finally:
            task.cancel()

    return StreamingResponse(events(), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.get("/api/deck/{job_id}")
async def deck_status(job_id: str) -> dict[str, Any]:
    if not deck_client.deck_available():
        raise HTTPException(status_code=503, detail="Deck engine not configured")
    try:
        st = await deck_client.job_status(job_id)
    except deck_client.DeckError as e:
        raise HTTPException(status_code=502, detail=str(e))
    out = {
        "status": st.get("status"),
        "progress": round(float(st.get("progress") or 0) * 100),
        "message": st.get("message", ""),
        "warnings": st.get("warnings", []),
    }
    if st.get("status") == "done":
        previews = st.get("previews") or []
        count = previews if isinstance(previews, int) else len(previews)
        out.update(deck_client.public_links(job_id, count, int(st.get("revision") or 0)))
    return out


@app.get("/api/create/capabilities")
async def create_capabilities() -> dict[str, Any]:
    return {
        "pdf": True,
        "docx": True,
        "presentation": deck_client.deck_available(),
        "images": images_available(),
        "editable_extensions": sorted(EDITABLE_EXTENSIONS | {".pdf"}),
    }


@app.post("/api/chat")
async def chat(body: ChatRequest) -> Any:
    messages = _build_messages(body)
    messages, trimmed = _cap_multimodal_images(messages, VLLM_MAX_IMAGES_PER_PROMPT)
    if trimmed:
        logger.warning(
            "Replaced %d older image(s) with a note (vLLM max %d per request)",
            trimmed,
            VLLM_MAX_IMAGES_PER_PROMPT,
        )

    backend, degraded = router.resolve(body.model)
    ctx = await _context_tokens(backend)
    max_tokens = min(body.max_tokens, max(256, ctx // 4))
    messages, fit_notes = _fit_messages(messages, ctx - max_tokens - 512)
    for note in fit_notes:
        logger.warning("chat context: %s", note)
    prompt_tokens = sum(_message_tokens(m) for m in messages)
    overflow = prompt_tokens + max_tokens + 256 - ctx
    if overflow > 0 and messages:
        _truncate_text_parts(messages[-1], int(overflow * float(os.getenv("CHARS_PER_TOKEN", "2.5"))))
        logger.warning("chat context: newest message truncated by ~%d tokens", overflow)
        prompt_tokens = sum(_message_tokens(m) for m in messages)
    max_tokens = max(64, min(max_tokens, ctx - prompt_tokens - 256))
    body.max_tokens = max_tokens

    has_images = any(
        isinstance(m.get("content"), list)
        and any(c.get("type") == "image_url" for c in m["content"] if isinstance(c, dict))
        for m in messages
    )

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
                **_llm_extra_kwargs(),
            )
            content = _assistant_text(completion.choices[0].message)
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
                **_llm_extra_kwargs(),
            )
            async for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if usage is not None and getattr(usage, "completion_tokens", None):
                    output_tokens = usage.completion_tokens
                if not chunk.choices:
                    continue
                try:
                    delta = chunk.choices[0].delta
                    piece = _delta_text(delta)
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
        "documents": ingest_available(),
        "libreoffice": ingest_capabilities()["libreoffice"],
        "allowed_extensions": ingest_capabilities()["allowed_extensions"],
        "multi_model_routing": len(router.backends) > 1,
        "file_creation": {
            "pdf": True,
            "docx": True,
            "presentation": deck_client.deck_available(),
            "images": images_available(),
        },
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
