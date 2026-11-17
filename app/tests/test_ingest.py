"""Golden tests for document ingest (no vLLM required)."""

import base64
import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from ingest import (
    ALLOWED_EXTENSIONS,
    IngestError,
    VLLM_MAX_IMAGES_PER_PROMPT,
    allowed_extensions,
    combine_results,
    effective_vision_image_cap,
    parse_bytes,
)

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _noise_png(side: int = 64) -> bytes:
    """Random-pixel PNG: big enough to pass the tiny-image and min-bytes filters."""
    fitz = pytest.importorskip("fitz")
    pix = fitz.Pixmap(fitz.csRGB, side, side, os.urandom(side * side * 3), False)
    return pix.tobytes("png")


def _minimal_docx_bytes(text: str = "Hello from Word.") -> bytes:
    # Smallest valid OPC package with one paragraph (no python-docx dependency in builder).
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
        rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
        doc = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body>
</w:document>"""
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", doc)
    return buf.getvalue()


def _minimal_pdf_bytes(unique: str) -> bytes:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text((72, 72), unique)
        return doc.tobytes()
    finally:
        doc.close()


def test_txt_and_md():
    r = parse_bytes("notes.txt", b"Line one\nLine two\n")
    assert "Line one" in r.text
    assert r.kind == "text"

    r2 = parse_bytes("readme.md", b"# Title\n\nBody **bold**.")
    assert "Title" in r2.text


def test_json_pretty():
    payload = {"a": 1, "b": [2, 3]}
    r = parse_bytes("data.json", json.dumps(payload).encode())
    assert '"a": 1' in r.text


def test_csv_rows():
    r = parse_bytes("t.csv", b"a,b\n1,2\n3,4\n")
    assert "1 | 2" in r.text


def test_pdf_text_extraction():
    fitz = pytest.importorskip("fitz")
    unique = (
        "INGEST_PDF_MARKER_42 - enough extracted text on this page to skip vision raster."
    )
    data = _minimal_pdf_bytes(unique)
    r = parse_bytes("paper.pdf", data)
    assert "INGEST_PDF_MARKER_42" in r.text
    assert r.kind == "pdf"
    assert len(r.images) == 0


def test_docx_paragraph():
    pytest.importorskip("docx")
    data = _minimal_docx_bytes("Table stakes for ingest.")
    r = parse_bytes("brief.docx", data)
    assert "Table stakes" in r.text
    assert r.kind == "docx"


def test_combine_blocks():
    results = [
        parse_bytes("a.txt", b"alpha"),
        parse_bytes("b.txt", b"beta"),
    ]
    batch = combine_results(results)
    assert "=== FILE: a.txt ===" in batch.combined_text
    assert "alpha" in batch.combined_text
    assert "beta" in batch.combined_text


def test_rejects_unknown_extension():
    with pytest.raises(IngestError, match="Unsupported"):
        parse_bytes("file.exe", b"MZ")


def test_allowed_extensions_non_empty():
    assert ".pdf" in ALLOWED_EXTENSIONS
    assert ".pdf" in allowed_extensions()


def test_vision_batches_respect_vllm_limit():
    from digest import _batches

    batches = _batches(list(range(11)), VLLM_MAX_IMAGES_PER_PROMPT)
    assert all(len(b) <= VLLM_MAX_IMAGES_PER_PROMPT for b in batches)
    assert sum(len(b) for b in batches) == 11
    assert effective_vision_image_cap() >= VLLM_MAX_IMAGES_PER_PROMPT


def test_docx_keeps_order_headings_and_tables():
    pytest.importorskip("docx")
    from docx import Document

    buf = io.BytesIO()
    doc = Document()
    doc.add_heading("Budget", level=1)
    doc.add_paragraph("Before table.")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Item"
    table.cell(0, 1).text = "Cost"
    table.cell(1, 0).text = "Laptops"
    table.cell(1, 1).text = "12,000"
    doc.add_paragraph("After table.")
    doc.save(buf)
    text = parse_bytes("plan.docx", buf.getvalue()).text
    assert "# Budget" in text
    assert "| Laptops | 12,000 |" in text
    assert text.index("Before table.") < text.index("Laptops") < text.index("After table.")


def test_image_upload_is_normalized():
    r = parse_bytes("photo.png", _noise_png())
    assert r.kind == "image"
    assert len(r.images) == 1 and r.images[0].mime == "image/png"

    with pytest.raises(IngestError):
        parse_bytes("broken.png", b"not an image")


def test_docx_many_images_all_kept_and_deduped():
    pytest.importorskip("docx")
    from docx import Document
    from docx.shared import Inches

    buf = io.BytesIO()
    doc = Document()
    pngs = [_noise_png() for _ in range(7)]
    for png in pngs + [pngs[0]]:
        doc.add_picture(io.BytesIO(png), width=Inches(1.0))
    doc.save(buf)
    r = parse_bytes("many.docx", buf.getvalue())
    assert len(r.images) == 7
    batch = combine_results([r])
    assert len(batch.images) == 7


def test_split_text_keeps_everything():
    from digest import _split_text

    text = "\n\n".join(f"para {i} " + "x" * 300 for i in range(200))
    chunks = _split_text(text, 5000)
    assert all(len(c) <= 5000 for c in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_docx_embedded_image():
    pytest.importorskip("docx")
    from docx import Document
    from docx.shared import Inches

    buf = io.BytesIO()
    doc = Document()
    doc.add_paragraph("Screenshot below")
    doc.add_picture(io.BytesIO(_noise_png()), width=Inches(1.0))
    doc.save(buf)
    r = parse_bytes("shots.docx", buf.getvalue())
    assert r.kind == "docx"
    assert len(r.images) >= 1
    assert any("embedded" in w for w in r.warnings)