"""File creation and editing: intent detection plus the report, edit, deck and image pipelines.

Each pipeline is an async generator of events that main.py streams to the browser as SSE:
  {"type": "progress", "message", "pct"}
  {"type": "file", "name", "mime", "b64", "size", "summary", "markdown", "kind"}
  {"type": "deck_job", "job_id", "title", "slide_count"}
  {"type": "chat"}            -> no file wanted, the browser falls back to normal chat
  {"type": "warning", "message"}
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import logging
import os
import re
from dataclasses import dataclass, field
from typing import AsyncIterator

from openai import AsyncOpenAI

import deck_client
from docspec import (
    Action,
    EditPlan,
    Intent,
    ReportOutline,
    ReportSpec,
    SectionContent,
    SectionPlan,
    spec_to_markdown,
)
from file_edit import EDITABLE_EXTENSIONS, MIME, TEXT_EXTENSIONS, apply_edit, edited_name, extension
from images import MAX_REPORT_IMAGES, ImageError, generate_image, images_available
from llm_structured import structured

logger = logging.getLogger(__name__)

WORDS_PER_PAGE = int(os.getenv("REPORT_WORDS_PER_PAGE", "380"))
MAX_REPORT_PAGES = int(os.getenv("MAX_REPORT_PAGES", "40"))
MAX_REPORT_SECTIONS = int(os.getenv("MAX_REPORT_SECTIONS", "16"))
SECTION_CONCURRENCY = int(os.getenv("SECTION_CONCURRENCY", "6"))
CONTEXT_CHARS_OUTLINE = int(os.getenv("CREATE_CONTEXT_CHARS", "60000"))
CONTEXT_CHARS_SECTION = int(os.getenv("CREATE_SECTION_CONTEXT_CHARS", "24000"))

DECK_ENGINE_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".csv", ".txt", ".md", ".json",
                          ".png", ".jpg", ".jpeg", ".webp"}

ACTIONS: tuple[str, ...] = ("chat", "pdf_report", "docx_document", "presentation", "edit_file", "image")

_CREATE_VERB = (
    r"(create|make|generate|produce|build|write|draft|prepare|design|export|turn .* into|convert|"
    r"stw[oó]rz|zr[oó]b|wygeneruj|przygotuj|napisz|opracuj|zaprojektuj|wyeksportuj|zamie[nń])"
)
_FILE_NOUN = (
    r"(pdf|docx|word|document|report|whitepaper|brief|ebook|e-book|presentation|deck|slides?|pptx|"
    r"powerpoint|image|picture|illustration|logo|poster|banner|file|"
    r"raport|dokument|prezentacj|slajd|obraz|ilustracj|grafik|plik|zdj[eę]ci)"
)
_EDIT = (
    r"\b(edit|update|revise|rewrite|fix|correct|change|modify|translate|proofread|shorten|extend|add|remove|"
    r"edytuj|popraw|zmie[nń]|zaktualizuj|przet[lł]umacz|skr[oó][cć]|rozszerz|dodaj|usu[nń])\b"
)
_IMAGE_WORDS = r"(image|picture|illustrat|photo|visual|drawing|graphic|obraz|ilustracj|zdj[eę]|grafik|rysun)"


def wants_file(prompt: str, has_files: bool) -> bool:
    """Cheap prefilter so ordinary chat messages never pay for an extra LLM call."""
    p = prompt.lower()
    if re.search(_CREATE_VERB, p) and re.search(_FILE_NOUN, p):
        return True
    return has_files and bool(re.search(_EDIT, p))


def asks_for_images(prompt: str) -> bool:
    return bool(re.search(_IMAGE_WORDS, prompt.lower()))


def _number_before(prompt: str, words: str) -> int:
    m = re.search(rf"(\d{{1,3}})\s*[- ]?\s*(?:{words})", prompt.lower())
    return int(m.group(1)) if m else 0


@dataclass
class UploadedFile:
    name: str
    data: bytes
    mime: str = "application/octet-stream"
    text: str = ""


@dataclass
class CreateRequest:
    prompt: str
    action: str = "auto"
    context: str = ""
    history: str = ""
    previous_markdown: str = ""
    previous_kind: str = ""
    language: str = ""
    files: list[UploadedFile] = field(default_factory=list)


def _progress(message: str, pct: int) -> dict:
    return {"type": "progress", "message": message, "pct": pct}


def _file_event(name: str, mime: str, data: bytes, kind: str, summary: str, markdown: str = "") -> dict:
    return {
        "type": "file",
        "name": name,
        "mime": mime,
        "kind": kind,
        "size": len(data),
        "b64": base64.b64encode(data).decode(),
        "summary": summary,
        "markdown": markdown,
    }


def _slug(title: str, fallback: str = "admind-file") -> str:
    s = re.sub(r"[^\w\s-]", "", title, flags=re.U).strip()
    s = re.sub(r"\s+", " ", s)[:80]
    return s or fallback


def _source_block(req: CreateRequest, limit: int) -> str:
    parts = []
    if req.previous_markdown.strip():
        parts.append("PREVIOUS VERSION OF THE FILE (revise it according to the request):\n" + req.previous_markdown)
    if req.context.strip():
        parts.append("SOURCE MATERIAL:\n" + req.context)
    for f in req.files:
        if f.text.strip():
            parts.append(f"FILE {f.name}:\n{f.text}")
    if req.history.strip():
        parts.append("RECENT CONVERSATION:\n" + req.history)
    text = "\n\n".join(parts)
    if len(text) > limit:
        text = text[:limit] + "\n[… source material truncated …]"
    return text


def _relevant(source: str, query: str, limit: int) -> str:
    """Keep the source chunks that share the most words with the section brief, in original order."""
    if len(source) <= limit:
        return source
    chunks = [source[i : i + 1500] for i in range(0, len(source), 1500)]
    terms = {w for w in re.findall(r"\w{4,}", query.lower())}
    scored = sorted(
        range(len(chunks)),
        key=lambda i: -sum(chunks[i].lower().count(t) for t in terms),
    )
    keep: set[int] = set()
    used = 0
    for i in scored:
        if used + len(chunks[i]) > limit:
            break
        keep.add(i)
        used += len(chunks[i])
    return "\n…\n".join(chunks[i] for i in sorted(keep))


class Creator:
    def __init__(self, client: AsyncOpenAI, model: str):
        self.client = client
        self.model = model

    async def intent(self, req: CreateRequest) -> Intent:
        has_files = bool(req.files)
        p = req.prompt
        system = (
            "Classify what the user wants. Actions: chat (answer in the conversation, no file), "
            "pdf_report (a PDF report/document/ebook), docx_document (an editable Word document), "
            "presentation (slides / deck / PowerPoint), edit_file (change a file the user attached, "
            "or revise the file generated earlier), image (a standalone picture). "
            "Only choose a file action when the user clearly asks for a file. "
            "wants_images is true only when the user explicitly asks for images or illustrations inside the file."
        )
        user = (
            f"Message: {p}\n\nAttached files: {', '.join(f.name for f in req.files) or 'none'}\n"
            f"A previously generated file exists: {'yes (' + req.previous_kind + ')' if req.previous_markdown else 'no'}"
        )
        try:
            intent = await structured(
                self.client,
                self.model,
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                Intent,
                max_tokens=300,
                temperature=0.0,
            )
        except Exception as e:
            logger.warning("intent classification failed: %s", e)
            intent = Intent(
                action="edit_file" if has_files else "pdf_report",
                title="",
                target_pages=0,
                slide_count=0,
                wants_images=False,
                language=req.language or "",
            )
        if req.action in ACTIONS:
            intent.action = req.action  # type: ignore[assignment]
        intent.wants_images = intent.wants_images and asks_for_images(p)
        intent.target_pages = intent.target_pages or _number_before(p, r"pages?|stron|str\.")
        intent.slide_count = intent.slide_count or _number_before(p, r"slides?|slajd")
        if req.language:
            intent.language = req.language
        return intent

    async def run(self, req: CreateRequest) -> AsyncIterator[dict]:
        explicit = req.action in ACTIONS and req.action != "chat"
        if not explicit and not wants_file(req.prompt, bool(req.files) or bool(req.previous_markdown)):
            yield {"type": "chat"}
            return
        yield _progress("Understanding the request…", 2)
        intent = await self.intent(req)
        action: Action = intent.action
        if action == "chat":
            yield {"type": "chat"}
            return
        if action == "edit_file":
            async for ev in self._edit(req, intent):
                yield ev
        elif action in ("pdf_report", "docx_document"):
            async for ev in self._report(req, intent, "pdf" if action == "pdf_report" else "docx"):
                yield ev
        elif action == "presentation":
            async for ev in self._deck(req, intent):
                yield ev
        elif action == "image":
            async for ev in self._image(req, intent):
                yield ev

    # ---------- reports (PDF / DOCX) ----------

    async def _report(self, req: CreateRequest, intent: Intent, fmt: str) -> AsyncIterator[dict]:
        requested = _number_before(req.prompt, r"pages?|stron|str\.")
        if req.previous_markdown and not requested:
            previous_pages = len(req.previous_markdown.split()) // WORDS_PER_PAGE + 2
            pages = max(2, min(MAX_REPORT_PAGES, previous_pages))
        else:
            pages = max(2, min(MAX_REPORT_PAGES, intent.target_pages or 6))
        total_words = pages * WORDS_PER_PAGE
        n_sections = max(3, min(MAX_REPORT_SECTIONS, round(total_words / 500)))
        language = intent.language or "the language of the user's message"
        images_on = intent.wants_images and images_available()
        if intent.wants_images and not images_available():
            yield {"type": "warning", "message": "Images were requested but no image generator is configured; the file has none."}

        source = _source_block(req, CONTEXT_CHARS_OUTLINE)
        yield _progress(f"Planning a {pages}-page {fmt.upper()} ({n_sections} sections)…", 6)
        visuals = "none, table, chart" + (", image" if images_on else "")
        outline = await structured(
            self.client,
            self.model,
            [
                {
                    "role": "system",
                    "content": (
                        "You plan professional reports for Admind, a creative agency. Write in "
                        f"{language}. Plan exactly {n_sections} sections with target_words summing to about "
                        f"{total_words}. Base every claim on the source material when it is provided; never invent "
                        "statistics. visual must be one of: " + visuals + ". Honour any charts or tables the user asks "
                        "for; otherwise use chart only when there are numbers worth plotting."
                        + (" A previous version is provided: keep its title, sections and substance, and change "
                           "only what the request asks for." if req.previous_markdown else "")
                        + (" Use image for at most " + str(MAX_REPORT_IMAGES) + " sections." if images_on else "")
                    ),
                },
                {"role": "user", "content": f"Request: {req.prompt}\n\n{source or '(no source material)'}"},
            ],
            ReportOutline,
            max_tokens=3000,
        )
        outline.sections = outline.sections[:MAX_REPORT_SECTIONS]
        for s in outline.sections:
            s.heading = re.sub(r"^\s*(?:(?:section|sekcja|rozdzia[lł]|chapter)\s*\d+|\d+[.:)])\s*[.:)\-–]?\s*", "", s.heading, flags=re.I) or s.heading
        if intent.title and not req.previous_markdown:
            outline.title = outline.title or intent.title

        sem = asyncio.Semaphore(SECTION_CONCURRENCY)
        done = 0
        progress_q: asyncio.Queue[str] = asyncio.Queue()

        async def write(idx: int, plan: SectionPlan) -> SectionContent:
            nonlocal done
            async with sem:
                ctx = _relevant(source, plan.heading + " " + plan.brief, CONTEXT_CHARS_SECTION)
                types = "paragraph, subheading, bullets, numbered, table, chart, callout, quote"
                if images_on and plan.visual == "image":
                    types += ", image (set image_prompt to a vivid description and caption)"
                try:
                    sec = await structured(
                        self.client,
                        self.model,
                        [
                            {
                                "role": "system",
                                "content": (
                                    f"You write one section of the report “{outline.title}” in {language}. "
                                    f"Write about {plan.target_words} words of substantive, specific prose. "
                                    f"Allowed block types: {types}. Do not repeat the section heading as a block. "
                                    "Use **bold** sparingly for key facts. Tables need headers and rows of equal "
                                    "length. Charts need categories and series with one numeric value per category. "
                                    + (f"You must include exactly one {plan.visual} block. " if plan.visual in ("table", "chart", "image") and (plan.visual != "image" or images_on) else "")
                                    + ("Use facts from the source material and never invent statistics."
                                       if source else
                                       "There is no source material: any figures you use must be labelled as illustrative estimates.")
                                ),
                            },
                            {
                                "role": "user",
                                "content": (
                                    f"Section {idx + 1}: {plan.heading}\nBrief: {plan.brief}\n\n"
                                    f"Full outline: {'; '.join(s.heading for s in outline.sections)}\n\n"
                                    f"{ctx or '(no source material)'}"
                                ),
                            },
                        ],
                        SectionContent,
                        max_tokens=min(6000, int(plan.target_words * 2.4) + 800),
                        temperature=0.5,
                    )
                except Exception as e:
                    logger.warning("section %d failed: %s", idx + 1, e)
                    sec = SectionContent(heading=plan.heading, blocks=[])
                sec.heading = plan.heading
                done += 1
                await progress_q.put(f"Wrote section {done}/{len(outline.sections)}: {plan.heading}")
                return sec

        tasks = [asyncio.create_task(write(i, s)) for i, s in enumerate(outline.sections)]
        while not all(t.done() for t in tasks) or not progress_q.empty():
            try:
                msg = await asyncio.wait_for(progress_q.get(), timeout=10)
                yield _progress(msg, 10 + int(70 * done / max(1, len(tasks))))
            except asyncio.TimeoutError:
                yield {"type": "keepalive"}
        sections = [t.result() for t in tasks]

        for sec in sections:
            sec.blocks = [b for b in sec.blocks if images_on or b.type != "image"]
        spec = ReportSpec(
            title=outline.title,
            subtitle=outline.subtitle,
            date=dt.date.today().strftime("%d %B %Y"),
            executive_summary=outline.executive_summary,
            sections=sections,
        )

        images: dict[tuple[int, int], bytes] = {}
        if images_on:
            wanted = [
                (si, bi, b.image_prompt or b.caption)
                for si, s in enumerate(spec.sections)
                for bi, b in enumerate(s.blocks)
                if b.type == "image"
            ][:MAX_REPORT_IMAGES]
            for n, (si, bi, prompt) in enumerate(wanted, 1):
                yield _progress(f"Generating image {n}/{len(wanted)}…", 82)
                try:
                    images[(si, bi)] = await generate_image(prompt)
                except ImageError as e:
                    yield {"type": "warning", "message": f"Image skipped: {e}"}

        yield _progress(f"Rendering the {fmt.upper()}…", 92)
        if fmt == "pdf":
            from render_pdf import render_pdf

            data = await asyncio.to_thread(render_pdf, spec, images)
        else:
            from render_docx import render_docx

            data = await asyncio.to_thread(render_docx, spec, images)
        words = sum(
            len(" ".join([b.text, *b.items]).split()) for s in spec.sections for b in s.blocks
        )
        summary = f"**{spec.title}**: {len(spec.sections)} sections, about {words:,} words" + (
            f", {len(images)} generated image(s)" if images else ""
        ) + "."
        yield _file_event(
            f"{_slug(spec.title)}.{fmt}",
            MIME[f".{fmt}"],
            data,
            fmt,
            summary,
            spec_to_markdown(spec),
        )

    # ---------- edits ----------

    async def _edit(self, req: CreateRequest, intent: Intent) -> AsyncIterator[dict]:
        target = next((f for f in req.files if extension(f.name) in EDITABLE_EXTENSIONS), None)
        if target is None:
            pdf = next((f for f in req.files if extension(f.name) == ".pdf"), None)
            if pdf is not None or req.previous_markdown:
                fmt = "docx" if req.previous_kind == "docx" else "pdf"
                if pdf is not None:
                    yield {"type": "warning", "message": "PDFs cannot be edited in place, so a revised branded PDF is generated from its content."}
                async for ev in self._report(req, intent, fmt):
                    yield ev
                return
            yield {"type": "error", "message": "Attach the file to edit (DOCX, PPTX, XLSX, TXT, MD, CSV or JSON)."}
            return

        ext = extension(target.name)
        yield _progress(f"Reading {target.name}…", 10)
        doc_text = target.text[:CONTEXT_CHARS_OUTLINE]
        rules = (
            "Return full_text with the complete new file content and leave the lists empty."
            if ext in TEXT_EXTENSIONS
            else "Use replacements whose find is copied exactly from the document text (short, unique spans "
            "work best; keep each within one paragraph). Use append_paragraphs only for new content at the end."
            + (" Use cell_edits for spreadsheet cells (sheet name and A1 reference)." if ext == ".xlsx" else "")
            + " Leave full_text empty."
        )
        yield _progress("Planning the edits…", 30)
        plan = await structured(
            self.client,
            self.model,
            [
                {"role": "system", "content": f"You edit the user's {ext} file precisely as asked, changing nothing else. {rules}"},
                {"role": "user", "content": f"Request: {req.prompt}\n\nDOCUMENT TEXT:\n{doc_text}"},
            ],
            EditPlan,
            max_tokens=8000 if ext in TEXT_EXTENSIONS else 4000,
            temperature=0.2,
        )
        yield _progress("Applying the edits…", 80)
        try:
            result = await asyncio.to_thread(apply_edit, target.name, target.data, plan)
        except ValueError as e:
            yield {"type": "error", "message": str(e)}
            return
        for w in result.warnings[:8]:
            yield {"type": "warning", "message": w}
        if result.applied == 0:
            yield {"type": "error", "message": "No changes could be applied. Try quoting the exact text to change."}
            return
        summary = f"{plan.summary} ({result.applied} change{'s' if result.applied != 1 else ''} applied.)"
        yield _file_event(edited_name(target.name), MIME.get(ext, target.mime), result.data, ext.lstrip("."), summary)

    # ---------- presentations ----------

    async def _deck(self, req: CreateRequest, intent: Intent) -> AsyncIterator[dict]:
        if not deck_client.deck_available():
            yield {"type": "error", "message": "Presentations need the Admind deck engine (set DECK_ENGINE_URL)."}
            return
        slides = max(3, min(25, intent.slide_count or 10))
        files = [(f.name, f.data, f.mime) for f in req.files if extension(f.name) in DECK_ENGINE_EXTENSIONS]
        skipped = [f.name for f in req.files if extension(f.name) not in DECK_ENGINE_EXTENSIONS]
        if skipped:
            yield {"type": "warning", "message": f"Not sent to the deck engine (unsupported type): {', '.join(skipped)}"}
        extra = _source_block(CreateRequest(prompt="", context=req.context, history=req.history,
                                            previous_markdown=req.previous_markdown), CONTEXT_CHARS_OUTLINE)
        if extra.strip():
            files.append(("context.txt", extra.encode("utf-8"), "text/plain"))
        yield _progress(f"Sending a {slides}-slide brief to the Admind deck engine…", 10)
        lang = (intent.language or "auto").lower()
        lang = "pl" if lang.startswith(("pl", "pol")) else "en" if lang.startswith(("en", "ang")) else "auto"
        job_id = await deck_client.create_job(req.prompt, slide_count=slides, language=lang, files=files)
        yield {"type": "deck_job", "job_id": job_id, "title": intent.title or "Presentation", "slide_count": slides}

    # ---------- standalone images ----------

    async def _image(self, req: CreateRequest, intent: Intent) -> AsyncIterator[dict]:
        if not images_available():
            yield {"type": "error", "message": "Image generation is not configured on this server (IMAGE_BASE_URL)."}
            return
        yield _progress("Generating the image…", 20)
        brand = bool(re.search(r"admind|brand|mark[ia]", req.prompt.lower()))
        data = await generate_image(req.prompt, brand_style=brand)
        yield _file_event(f"{_slug(intent.title or req.prompt[:40], 'image')}.jpg", "image/jpeg", data, "image",
                          "Generated image.")
