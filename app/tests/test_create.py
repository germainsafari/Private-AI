"""File creation and editing tests (no vLLM, deck engine or image provider required)."""

import base64
import hashlib
import hmac
import io
import json

import fitz
import pytest

from docspec import EditPlan, TextEdit, CellEdit, spec_to_markdown
from file_edit import apply_edit, edited_name
from render_docx import render_docx
from render_pdf import render_pdf
from tests.fixtures_report import sample_spec


def test_pdf_report_is_branded_and_long():
    pdf = render_pdf(sample_spec(12))
    doc = fitz.open(stream=pdf, filetype="pdf")
    assert doc.page_count >= 10
    text = "".join(p.get_text() for p in doc)
    assert "Contents" in text
    assert "zażółć gęślą jaźń" in text
    assert doc.get_toc()


def test_pdf_embeds_generated_image():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 40), (41, 217, 194)).save(buf, format="JPEG")
    spec = sample_spec(2)
    spec.sections[0].blocks.append(spec.sections[0].blocks[0].model_copy(update={"type": "image", "caption": "Teal"}))
    images = {(0, len(spec.sections[0].blocks) - 1): buf.getvalue()}
    doc = fitz.open(stream=render_pdf(spec, images), filetype="pdf")
    assert sum(len(p.get_images()) for p in doc) > sum(
        len(p.get_images()) for p in fitz.open(stream=render_pdf(spec), filetype="pdf")
    )


def test_docx_report_has_headings_tables_and_charts():
    from docx import Document

    d = Document(io.BytesIO(render_docx(sample_spec(3))))
    texts = [p.text for p in d.paragraphs]
    assert any(t.startswith("Market review 1") for t in texts)
    assert d.tables
    assert d.inline_shapes  # logo + rendered charts


def test_markdown_round_trip_keeps_structure():
    md = spec_to_markdown(sample_spec(2))
    assert md.startswith("# ")
    assert "## Executive summary" in md


def _docx_with_bold() -> bytes:
    from docx import Document

    d = Document()
    p = d.add_paragraph("Launch in ")
    p.add_run("March 2026").bold = True
    p.add_run(" with Jan Kowalski.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_docx_edit_preserves_run_formatting():
    from docx import Document

    plan = EditPlan(summary="", replacements=[TextEdit(find="March", replace="April")],
                    append_paragraphs=["New closing line."], cell_edits=[], full_text="")
    out = apply_edit("brief.docx", _docx_with_bold(), plan)
    d = Document(io.BytesIO(out.data))
    assert d.paragraphs[0].text == "Launch in April 2026 with Jan Kowalski."
    assert d.paragraphs[0].runs[1].bold
    assert d.paragraphs[-1].text == "New closing line."
    assert out.applied == 2


def test_edit_across_runs_and_missing_text_warns():
    plan = EditPlan(summary="", replacements=[TextEdit(find="2026 with Jan", replace="2027 with Anna"),
                                              TextEdit(find="nowhere", replace="x")],
                    append_paragraphs=[], cell_edits=[], full_text="")
    out = apply_edit("brief.docx", _docx_with_bold(), plan)
    assert out.applied == 1
    assert out.warnings


def test_xlsx_cell_edit():
    from openpyxl import Workbook, load_workbook

    wb = Workbook()
    wb.active.title = "Budget"
    wb.active["A1"] = "Total"
    buf = io.BytesIO()
    wb.save(buf)
    plan = EditPlan(summary="", replacements=[], append_paragraphs=[],
                    cell_edits=[CellEdit(sheet="Budget", cell="B1", value="1250.5")], full_text="")
    out = apply_edit("b.xlsx", buf.getvalue(), plan)
    assert load_workbook(io.BytesIO(out.data))["Budget"]["B1"].value == 1250.5


def test_text_full_replacement_and_names():
    plan = EditPlan(summary="", replacements=[], append_paragraphs=[], cell_edits=[], full_text="hello")
    assert apply_edit("notes.md", b"old", plan).data == b"hello"
    assert edited_name("notes.md") == "notes (edited).md"
    assert edited_name("notes (edited).md") == "notes (edited).md"
    with pytest.raises(ValueError):
        apply_edit("scan.pdf", b"%PDF", plan)


@pytest.mark.parametrize(
    "prompt,files,expected",
    [
        ("Create a 20-page PDF report about our Q3 results", False, True),
        ("Przygotuj prezentację o employer brandingu", False, True),
        ("make me a deck for the board", False, True),
        ("What is employer branding?", False, False),
        ("Write a poem about Mondays", False, False),
        ("Fix the typos", True, True),
        ("Summarise this", True, False),
    ],
)
def test_file_request_prefilter(prompt, files, expected):
    from creator import wants_file

    assert wants_file(prompt, files) is expected


def test_image_requests_need_explicit_words():
    from creator import asks_for_images

    assert asks_for_images("Add two illustrations to the report")
    assert asks_for_images("z ilustracjami")
    assert not asks_for_images("A 10 page report on retention")


def test_deck_token_matches_engine_format(monkeypatch):
    import deck_client

    monkeypatch.setattr(deck_client, "DECK_ENGINE_TOKEN_SECRET", "s3cret")
    token = deck_client.mint_token(60)
    payload, sig = token.split(".")
    expected = base64.urlsafe_b64encode(hmac.new(b"s3cret", payload.encode(), hashlib.sha256).digest()).rstrip(b"=")
    assert sig.encode() == expected
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert claims["sub"] == "private-ai"
