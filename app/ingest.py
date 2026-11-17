"""Parse uploaded documents into text and optional vision images (text-first)."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_UPLOAD_BYTES = int(float(os.getenv("MAX_UPLOAD_MB", "25")) * 1024 * 1024)
# Safety ceiling only; oversized text is condensed by digest.py, not dropped.
MAX_EXTRACT_CHARS = int(os.getenv("MAX_EXTRACT_CHARS", "3000000"))
MAX_TABLE_ROWS = int(os.getenv("MAX_TABLE_ROWS", "200000"))
# vLLM --limit-mm-per-prompt image count; images are transcribed in batches of this size.
VLLM_MAX_IMAGES_PER_PROMPT = max(1, int(os.getenv("VLLM_MAX_IMAGES_PER_PROMPT", "4")))
MAX_VISION_IMAGES = int(os.getenv("MAX_VISION_IMAGES", "60"))
MAX_VISION_PAGES = int(os.getenv("MAX_VISION_PAGES", "60"))
MAX_EMBEDDED_IMAGES = int(os.getenv("MAX_EMBEDDED_IMAGES", "60"))
MIN_EMBEDDED_IMAGE_BYTES = int(os.getenv("MIN_EMBEDDED_IMAGE_BYTES", "3000"))
MAX_IMAGE_SIDE = int(os.getenv("MAX_IMAGE_SIDE", "1600"))
MIN_PDF_TEXT_CHARS = int(os.getenv("MIN_PDF_TEXT_CHARS", "40"))
PDF_RENDER_SCALE = float(os.getenv("PDF_RENDER_SCALE", "1.5"))
PDF_IMAGE_COVERAGE_THRESHOLD = float(os.getenv("PDF_IMAGE_COVERAGE_THRESHOLD", "0.55"))
MAX_EMBEDDED_IMAGE_BYTES = int(os.getenv("MAX_EMBEDDED_IMAGE_MB", "15")) * 1024 * 1024

ALLOWED_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".docx",
        ".txt",
        ".md",
        ".markdown",
        ".json",
        ".csv",
        ".xlsx",
        ".pptx",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".tif",
        ".tiff",
    }
)

IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
)

LEGACY_OFFICE_EXTENSIONS = frozenset({".doc", ".odt", ".rtf"})

_MAGIC_MIME = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # needs WEBP at offset 8 — checked below
)


@dataclass
class VisionImage:
    mime: str
    data_base64: str
    source: str = ""

    def to_data_url(self) -> str:
        return f"data:{self.mime};base64,{self.data_base64}"


@dataclass
class FileIngestResult:
    name: str
    kind: str
    text: str
    warnings: list[str] = field(default_factory=list)
    images: list[VisionImage] = field(default_factory=list)
    truncated: bool = False


@dataclass
class IngestBatch:
    files: list[FileIngestResult]
    combined_text: str
    images: list[VisionImage]
    warnings: list[str]

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "files": [
                {
                    "name": f.name,
                    "kind": f.kind,
                    "text": f.text,
                    "warnings": f.warnings,
                    "image_count": len(f.images),
                    "truncated": f.truncated,
                }
                for f in self.files
            ],
            "combined_text": self.combined_text,
            "images": [
                {
                    "mime": img.mime,
                    "data_base64": img.data_base64,
                    "source": img.source,
                }
                for img in self.images
            ],
            "warnings": self.warnings,
        }


class IngestError(ValueError):
    pass


def _libreoffice_binary() -> str | None:
    for key in ("SOFFICE_PATH", "LIBREOFFICE_PATH"):
        val = os.getenv(key, "").strip().strip('"')
        if val and Path(val).is_file():
            return val
    return shutil.which("soffice") or shutil.which("soffice.exe")


def libreoffice_available() -> bool:
    return _libreoffice_binary() is not None


def allowed_extensions() -> frozenset[str]:
    ext = set(ALLOWED_EXTENSIONS)
    if libreoffice_available():
        ext |= LEGACY_OFFICE_EXTENSIONS
    return frozenset(ext)


def _ext(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    dot = base.rfind(".")
    if dot < 0:
        return ""
    return base[dot:].lower()


def _mime_from_blob(blob: bytes, filename: str = "") -> str:
    for magic, mime in _MAGIC_MIME:
        if blob.startswith(magic):
            if mime == "image/webp" and len(blob) > 12 and blob[8:12] != b"WEBP":
                continue
            return mime
    ext = _ext(filename).lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }.get(ext, "application/octet-stream")


def effective_vision_image_cap() -> int:
    """Total images per ingest that will be transcribed by the vision model."""
    return MAX_VISION_IMAGES


def _vision_slots_remaining(images: list[VisionImage]) -> int:
    return max(0, effective_vision_image_cap() - len(images))


def _normalize_image(blob: bytes) -> bytes | None:
    """Re-encode any raster PyMuPDF can read as RGB PNG, downscaled; None if unreadable."""
    try:
        import fitz
    except ImportError:
        return blob if _mime_from_blob(blob).startswith("image/") else None
    try:
        pix = fitz.Pixmap(blob)
        if pix.alpha:
            pix = fitz.Pixmap(pix, 0)
        if pix.colorspace is None or pix.colorspace.n != 3:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        while max(pix.width, pix.height) > MAX_IMAGE_SIDE:
            pix.shrink(1)
        if pix.width < 16 or pix.height < 16:
            return None
        return pix.tobytes("png")
    except Exception:
        return None


def _append_vision_image(
    images: list[VisionImage],
    blob: bytes,
    source: str,
    warnings: list[str],
    *,
    filename: str = "",
) -> bool:
    if _vision_slots_remaining(images) <= 0:
        warnings.append(f"{source}: vision image cap ({effective_vision_image_cap()}) reached")
        return False
    if len(blob) > MAX_EMBEDDED_IMAGE_BYTES:
        warnings.append(
            f"{source}: image skipped (over {MAX_EMBEDDED_IMAGE_BYTES // (1024 * 1024)} MB)"
        )
        return False
    png = _normalize_image(blob)
    if png is None:
        warnings.append(f"{source}: unsupported or unreadable image format; skipped")
        return False
    images.append(
        VisionImage(
            mime="image/png",
            data_base64=base64.standard_b64encode(png).decode("ascii"),
            source=source,
        )
    )
    return True


def _extract_ooxml_media(
    data: bytes,
    media_prefix: str,
    name: str,
    images: list[VisionImage],
    warnings: list[str],
    *,
    max_count: int,
) -> int:
    added = 0
    seen: set[str] = set()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = sorted(n for n in zf.namelist() if n.startswith(media_prefix) and not n.endswith("/"))
            for part in names:
                if added >= max_count:
                    warnings.append(f"{name}: embedded image cap ({max_count}) reached")
                    break
                if _vision_slots_remaining(images) <= 0:
                    warnings.append(f"{name}: vision cap reached; remaining embedded images omitted")
                    break
                blob = zf.read(part)
                if len(blob) < MIN_EMBEDDED_IMAGE_BYTES:
                    continue
                digest = hashlib.sha1(blob).hexdigest()
                if digest in seen:
                    continue
                seen.add(digest)
                part_name = part.rsplit("/", 1)[-1]
                if _append_vision_image(
                    images, blob, f"{name} embedded {part_name}", warnings, filename=part_name
                ):
                    added += 1
    except zipfile.BadZipFile:
        warnings.append(f"{name}: could not read package media")
    if added:
        warnings.append(f"{name}: {added} embedded image(s) transcribed by the vision model")
    return added


def _truncate(text: str, warnings: list[str], label: str) -> tuple[str, bool]:
    if len(text) <= MAX_EXTRACT_CHARS:
        return text, False
    warnings.append(f"{label}: text truncated to {MAX_EXTRACT_CHARS} characters")
    return text[:MAX_EXTRACT_CHARS] + "\n\n[… truncated …]", True


def _decode_text(data: bytes, name: str, warnings: list[str]) -> str:
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data).best()
        if best is not None:
            return str(best)
    except Exception:
        pass
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    warnings.append(f"{name}: could not detect encoding; decoded with replacement")
    return data.decode("utf-8", errors="replace")


def _parse_plain(data: bytes, name: str, warnings: list[str]) -> str:
    text = _decode_text(data, name, warnings)
    text, _ = _truncate(text, warnings, name)
    return text


def _parse_json(data: bytes, name: str, warnings: list[str]) -> str:
    raw = _decode_text(data, name, warnings)
    try:
        obj = json.loads(raw)
        pretty = json.dumps(obj, indent=2, ensure_ascii=False)
        pretty, _ = _truncate(pretty, warnings, name)
        return pretty
    except json.JSONDecodeError:
        warnings.append(f"{name}: invalid JSON; returning raw text")
        return _parse_plain(data, name, warnings)


def _parse_csv(data: bytes, name: str, warnings: list[str]) -> str:
    raw = _decode_text(data, name, warnings)
    buf = io.StringIO(raw)
    reader = csv.reader(buf)
    rows: list[str] = []
    for i, row in enumerate(reader):
        if i > MAX_TABLE_ROWS:
            warnings.append(f"{name}: CSV truncated after {MAX_TABLE_ROWS} rows")
            break
        rows.append(" | ".join(row))
    text = "\n".join(rows)
    text, _ = _truncate(text, warnings, name)
    return text


def _page_image_coverage(page: Any) -> float:
    try:
        page_area = float(page.rect.width * page.rect.height) or 1.0
        covered = 0.0
        for block in page.get_image_info(xrefs=True):
            bbox = block.get("bbox")
            if not bbox or len(bbox) < 4:
                continue
            x0, y0, x1, y1 = bbox[:4]
            covered += max(0.0, (x1 - x0) * (y1 - y0))
        return min(1.0, covered / page_area)
    except Exception:
        return 0.0


def _pdf_page_needs_vision(page: Any, page_text: str) -> tuple[bool, str]:
    if len(page_text) < MIN_PDF_TEXT_CHARS:
        return True, "little extractable text; page sent to vision"
    coverage = _page_image_coverage(page)
    if coverage >= PDF_IMAGE_COVERAGE_THRESHOLD:
        return True, f"image-heavy page (coverage {coverage:.0%}); also sent to vision"
    return False, ""


def _parse_pdf(
    data: bytes, name: str, warnings: list[str], images: list[VisionImage]
) -> str:
    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise IngestError("PDF support requires pymupdf") from e

    doc = fitz.open(stream=data, filetype="pdf")
    parts: list[str] = []
    pdf_vision_pages = 0

    try:
        for page_no in range(doc.page_count):
            page = doc[page_no]
            page_text = (page.get_text() or "").strip()
            parts.append(f"--- page {page_no + 1} ---")

            needs_vision, reason = _pdf_page_needs_vision(page, page_text)
            if page_text:
                parts.append(page_text)

            if needs_vision:
                if pdf_vision_pages >= MAX_VISION_PAGES:
                    warnings.append(
                        f"{name} page {page_no + 1}: {reason} but per-file page cap "
                        f"({MAX_VISION_PAGES}) reached"
                    )
                    continue
                if _vision_slots_remaining(images) <= 0:
                    warnings.append(
                        f"{name} page {page_no + 1}: {reason} but global vision cap reached"
                    )
                    continue
                mat = fitz.Matrix(PDF_RENDER_SCALE, PDF_RENDER_SCALE)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                png = pix.tobytes("png")
                if _append_vision_image(
                    images,
                    png,
                    f"{name} page {page_no + 1}",
                    warnings,
                    filename=f"page{page_no + 1}.png",
                ):
                    pdf_vision_pages += 1
                    warnings.append(f"{name} page {page_no + 1}: {reason}")
    finally:
        doc.close()

    text = "\n".join(parts)
    text, _ = _truncate(text, warnings, name)
    return text


def _parse_docx(
    data: bytes, name: str, warnings: list[str], images: list[VisionImage]
) -> str:
    try:
        from docx import Document
    except ImportError as e:
        raise IngestError("DOCX support requires python-docx") from e

    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(io.BytesIO(data))
    parts: list[str] = []

    def para_line(para: Paragraph) -> str:
        t = (para.text or "").strip()
        if not t:
            return ""
        style = (para.style.name if para.style is not None else "") or ""
        if style.startswith("Heading"):
            level = "".join(ch for ch in style if ch.isdigit()) or "1"
            return f"{'#' * min(int(level), 6)} {t}"
        if style == "Title":
            return f"# {t}"
        if "List" in style:
            return f"- {t}"
        return t

    def table_lines(table: Table, idx: int) -> list[str]:
        lines = [f"[table {idx}]"]
        for row in table.rows:
            cells: list[str] = []
            prev = None
            for cell in row.cells:
                # merged cells repeat the same object across the span
                if cell._tc is prev:
                    continue
                prev = cell._tc
                cells.append(" ".join((cell.text or "").split()))
            lines.append("| " + " | ".join(cells) + " |")
        return lines

    table_idx = 0
    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            line = para_line(Paragraph(child, doc))
            if line:
                parts.append(line)
        elif tag == "tbl":
            table_idx += 1
            parts.extend(table_lines(Table(child, doc), table_idx))

    ns_w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    box_texts: list[str] = []
    for box in body.iter(f"{ns_w}txbxContent"):
        for p in box.iter(f"{ns_w}p"):
            t = "".join(node.text or "" for node in p.iter(f"{ns_w}t")).strip()
            if t and t not in box_texts:
                box_texts.append(t)
    if box_texts:
        parts.append("[text boxes / shapes]")
        parts.extend(box_texts)

    header_footer: list[str] = []
    for section in doc.sections:
        for part in (section.header, section.footer):
            try:
                for para in part.paragraphs:
                    t = (para.text or "").strip()
                    if t and t not in header_footer:
                        header_footer.append(t)
            except Exception:
                continue
    if header_footer:
        parts.append("[headers / footers]")
        parts.extend(header_footer)

    if not parts:
        warnings.append(f"{name}: no paragraph or table text extracted")

    _extract_ooxml_media(
        data, "word/media/", name, images, warnings, max_count=MAX_EMBEDDED_IMAGES
    )

    text = "\n".join(parts)
    text, _ = _truncate(text, warnings, name)
    return text


def _parse_xlsx(data: bytes, name: str, warnings: list[str]) -> str:
    try:
        from openpyxl import load_workbook
    except ImportError as e:
        raise IngestError("XLSX support requires openpyxl") from e

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts: list[str] = []
    try:
        for sheet in wb.worksheets:
            parts.append(f"=== sheet: {sheet.title} ===")
            row_count = 0
            for row in sheet.iter_rows(values_only=True):
                row_count += 1
                if row_count > MAX_TABLE_ROWS:
                    warnings.append(
                        f"{name} sheet {sheet.title}: truncated after {MAX_TABLE_ROWS} rows"
                    )
                    break
                cells = ["" if v is None else str(v) for v in row]
                if any(c.strip() for c in cells):
                    parts.append(" | ".join(cells))
    finally:
        wb.close()

    text = "\n".join(parts)
    text, _ = _truncate(text, warnings, name)
    return text


def _parse_pptx(
    data: bytes, name: str, warnings: list[str], images: list[VisionImage]
) -> str:
    try:
        from pptx import Presentation
    except ImportError as e:
        raise IngestError("PPTX support requires python-pptx") from e

    prs = Presentation(io.BytesIO(data))
    parts: list[str] = []
    for si, slide in enumerate(prs.slides, start=1):
        parts.append(f"--- slide {si} ---")
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text and shape.text.strip():
                parts.append(shape.text.strip())
            if shape.has_table:
                table = shape.table
                parts.append("[table]")
                for row in table.rows:
                    cells = [((c.text or "").strip()) for c in row.cells]
                    parts.append(" | ".join(cells))
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
            notes = (slide.notes_slide.notes_text_frame.text or "").strip()
            if notes:
                parts.append(f"[speaker notes]\n{notes}")

    _extract_ooxml_media(
        data, "ppt/media/", name, images, warnings, max_count=MAX_EMBEDDED_IMAGES
    )

    text = "\n".join(parts)
    text, _ = _truncate(text, warnings, name)
    return text


def _parse_via_libreoffice(data: bytes, name: str, ext: str, warnings: list[str]) -> str:
    binary = _libreoffice_binary()
    if not binary:
        raise IngestError(
            f"{ext} requires LibreOffice. Set SOFFICE_PATH or install soffice on PATH."
        )
    timeout = int(os.getenv("SOFFICE_TIMEOUT_SECONDS", "120"))
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        inp = td_path / f"upload{ext}"
        inp.write_bytes(data)
        try:
            proc = subprocess.run(
                [
                    binary,
                    "--headless",
                    "--nologo",
                    "--nofirststartwizard",
                    "--convert-to",
                    "txt:Text",
                    "--outdir",
                    str(td_path),
                    str(inp),
                ],
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise IngestError(f"LibreOffice timed out converting {name}") from e
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace")
            raise IngestError(f"LibreOffice failed for {name}: {err[:300]}")

        out = td_path / "upload.txt"
        if not out.is_file():
            txt_files = list(td_path.glob("*.txt"))
            if not txt_files:
                raise IngestError(f"LibreOffice produced no text output for {name}")
            out = txt_files[0]
        text = out.read_text(encoding="utf-8", errors="replace")
        warnings.append(f"{name}: converted from {ext} via LibreOffice")
        text, _ = _truncate(text, warnings, name)
        return text


_PARSERS: dict[str, Any] = {
    ".txt": lambda d, n, w, _i: ("text", _parse_plain(d, n, w)),
    ".md": lambda d, n, w, _i: ("markdown", _parse_plain(d, n, w)),
    ".markdown": lambda d, n, w, _i: ("markdown", _parse_plain(d, n, w)),
    ".json": lambda d, n, w, _i: ("json", _parse_json(d, n, w)),
    ".csv": lambda d, n, w, _i: ("csv", _parse_csv(d, n, w)),
    ".pdf": lambda d, n, w, i: ("pdf", _parse_pdf(d, n, w, i)),
    ".docx": lambda d, n, w, i: ("docx", _parse_docx(d, n, w, i)),
    ".xlsx": lambda d, n, w, _i: ("xlsx", _parse_xlsx(d, n, w)),
    ".pptx": lambda d, n, w, i: ("pptx", _parse_pptx(d, n, w, i)),
}


def parse_bytes(filename: str, data: bytes) -> FileIngestResult:
    if len(data) > MAX_UPLOAD_BYTES:
        raise IngestError(
            f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB): {filename}"
        )

    ext = _ext(filename)
    allowed = allowed_extensions()
    if ext not in allowed:
        hint = ""
        if ext in LEGACY_OFFICE_EXTENSIONS:
            hint = " Install LibreOffice locally and set SOFFICE_PATH for .doc/.odt/.rtf."
        raise IngestError(
            f"Unsupported type {ext or '(no extension)'}: {filename}. "
            f"Allowed: {', '.join(sorted(allowed))}.{hint}"
        )

    warnings: list[str] = []
    file_images: list[VisionImage] = []

    if ext in IMAGE_EXTENSIONS:
        if not _append_vision_image(file_images, data, filename, warnings, filename=filename):
            raise IngestError(f"Could not read image: {filename}")
        return FileIngestResult(
            name=filename, kind="image", text="", warnings=warnings, images=file_images
        )

    if ext in LEGACY_OFFICE_EXTENSIONS:
        kind = ext.lstrip(".")
        text = _parse_via_libreoffice(data, filename, ext, warnings)
    else:
        parser = _PARSERS.get(ext)
        if not parser:
            raise IngestError(f"No parser for {ext}")
        kind, text = parser(data, filename, warnings, file_images)

    truncated = "[… truncated …]" in text
    return FileIngestResult(
        name=filename,
        kind=kind,
        text=text,
        warnings=warnings,
        images=file_images,
        truncated=truncated,
    )


def combine_results(results: list[FileIngestResult]) -> IngestBatch:
    """Join per-file text. Images stay on each result; digest.py transcribes them first."""
    blocks: list[str] = []
    all_images: list[VisionImage] = []
    all_warnings: list[str] = []

    catalog = [f"- {r.name} ({r.kind}, {len(r.text):,} chars)" for r in results]
    blocks.append("## Sources\n" + "\n".join(catalog))

    for r in results:
        blocks.append(f"=== FILE: {r.name} ===")
        if r.text.strip():
            blocks.append(r.text.strip())
        else:
            blocks.append("(no extractable text)")
        all_warnings.extend(r.warnings)
        all_images.extend(r.images)

    combined = "\n\n".join(blocks)
    return IngestBatch(
        files=results,
        combined_text=combined,
        images=all_images,
        warnings=all_warnings,
    )


def ingest_available() -> bool:
    try:
        import fitz  # noqa: F401
        from docx import Document  # noqa: F401

        return True
    except ImportError:
        return False


def ingest_capabilities() -> dict[str, Any]:
    return {
        "documents": ingest_available(),
        "libreoffice": libreoffice_available(),
        "allowed_extensions": sorted(allowed_extensions()),
        "max_upload_mb": float(os.getenv("MAX_UPLOAD_MB", "25")),
        "max_extract_chars": MAX_EXTRACT_CHARS,
        "max_vision_images": effective_vision_image_cap(),
        "vllm_max_images_per_prompt": VLLM_MAX_IMAGES_PER_PROMPT,
        "max_vision_pages": MAX_VISION_PAGES,
        "max_embedded_images": MAX_EMBEDDED_IMAGES,
        "legacy_office_requires_libreoffice": True,
    }