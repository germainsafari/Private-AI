"""Render a ReportSpec into an editable, Admind-branded Word document."""

from __future__ import annotations

import io
import re
from datetime import date

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor

from brand import BLACK, CORAL, CREAM_LIGHT, DOCX_FONT, GREY, LOGO, PETROL, TEAL, hex_rgb
from charts import chart_png
from docspec import Block, ReportSpec


def _rgb(hex_: str) -> RGBColor:
    return RGBColor(*hex_rgb(hex_))


def _shade(cell, hex_: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_)
    tc_pr.append(shd)


def _field(paragraph, instr: str) -> None:
    run = paragraph.add_run()
    for kind, text in (("begin", None), (None, instr), ("separate", None), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = text
        run._r.append(el)


def add_inline(paragraph, text: str, size: float | None = None, color: str | None = None) -> None:
    """Append text to a paragraph, honouring **bold** and *italic* markdown."""
    for part in re.split(r"(\*\*.+?\*\*|\*[^*\s][^*]*?\*)", text or ""):
        if not part:
            continue
        run = paragraph.add_run()
        if part.startswith("**") and part.endswith("**"):
            run.text, run.bold = part[2:-2], True
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            run.text, run.italic = part[1:-1], True
        else:
            run.text = part
        if size:
            run.font.size = Pt(size)
        if color:
            run.font.color.rgb = _rgb(color)


def _heading(doc, text: str, level: int) -> None:
    p = doc.add_heading(level=level)
    r = p.add_run(text.rstrip("_ "))
    r.font.color.rgb = _rgb(BLACK if level == 1 else PETROL)
    if level == 1:
        u = p.add_run("_")
        u.font.color.rgb = _rgb(CORAL)


def _setup_styles(doc) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = DOCX_FONT
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), DOCX_FONT)
    for name, size in (("Heading 1", 20), ("Heading 2", 13.5), ("Title", 32)):
        s = doc.styles[name]
        s.font.name = DOCX_FONT
        s.font.size = Pt(size)
        s.font.bold = True
        s.font.color.rgb = _rgb(BLACK)
    settings = doc.settings.element
    upd = OxmlElement("w:updateFields")
    upd.set(qn("w:val"), "true")
    settings.append(upd)


def _table(doc, b: Block) -> None:
    ncols = max([len(b.headers)] + [len(r) for r in b.rows]) or 1
    rows = ([b.headers] if b.headers else []) + b.rows[:400]
    t = doc.add_table(rows=len(rows), cols=ncols)
    t.style = "Table Grid"
    for i, row in enumerate(rows):
        for j in range(ncols):
            cell = t.cell(i, j)
            cell.text = ""
            add_inline(cell.paragraphs[0], str(row[j]) if j < len(row) else "", size=9)
            if i == 0 and b.headers:
                for r in cell.paragraphs[0].runs:
                    r.bold = True
                _shade(cell, TEAL)
            elif (i - (1 if b.headers else 0)) % 2 == 1:
                _shade(cell, CREAM_LIGHT)
    if b.caption:
        cap = doc.add_paragraph()
        add_inline(cap, b.caption, size=8.5, color=GREY)
    doc.add_paragraph()


def _callout(doc, text: str, bar: str) -> None:
    t = doc.add_table(rows=1, cols=1)
    cell = t.cell(0, 0)
    _shade(cell, CREAM_LIGHT)
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), "24")
    left.set(qn("w:color"), bar)
    borders.append(left)
    tc_pr.append(borders)
    cell.text = ""
    add_inline(cell.paragraphs[0], text, size=11)
    doc.add_paragraph()


def render_docx(spec: ReportSpec, images: dict[tuple[int, int], bytes] | None = None) -> bytes:
    images = images or {}
    doc = Document()
    _setup_styles(doc)
    sec0 = doc.sections[0]
    sec0.page_width, sec0.page_height = Mm(210), Mm(297)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec0, side, Mm(20))

    if LOGO.is_file():
        doc.add_picture(str(LOGO), width=Mm(16))
    for _ in range(6):
        doc.add_paragraph()
    title = doc.add_paragraph()
    add_inline(title, spec.title.rstrip("_ "), size=32)
    for r in title.runs:
        r.bold = True
    u = title.add_run("_")
    u.font.size, u.font.color.rgb, u.bold = Pt(32), _rgb(CORAL), True
    if spec.subtitle:
        add_inline(doc.add_paragraph(), spec.subtitle, size=15, color=GREY)
    add_inline(doc.add_paragraph(), spec.date or date.today().strftime("%d %B %Y"), size=10, color=GREY)

    body = doc.add_section(WD_SECTION.NEW_PAGE)
    body.footer.is_linked_to_previous = False
    fp = body.footer.paragraphs[0]
    add_inline(fp, f"{spec.title[:80]}   ·   ", size=8, color=GREY)
    _field(fp, "PAGE")

    if len(spec.sections) >= 3:
        _heading(doc, "Contents", 1)
        _field(doc.add_paragraph(), 'TOC \\o "1-2" \\h \\z \\u')
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    if spec.executive_summary:
        _heading(doc, "Executive summary", 1)
        for s in spec.executive_summary:
            add_inline(doc.add_paragraph(style="List Bullet"), s)
        doc.add_paragraph()

    for si, sec in enumerate(spec.sections):
        _heading(doc, sec.heading, 1)
        for bi, b in enumerate(sec.blocks):
            if b.type == "paragraph" and b.text.strip():
                add_inline(doc.add_paragraph(), b.text)
            elif b.type == "subheading" and b.text.strip():
                _heading(doc, b.text, 2)
            elif b.type in ("bullets", "numbered"):
                style = "List Bullet" if b.type == "bullets" else "List Number"
                for item in b.items:
                    add_inline(doc.add_paragraph(style=style), item)
            elif b.type == "table":
                _table(doc, b)
            elif b.type == "chart" and b.chart:
                png = chart_png(b.chart)
                if png:
                    doc.add_picture(io.BytesIO(png), width=Mm(165))
                    if b.caption:
                        add_inline(doc.add_paragraph(), b.caption, size=8.5, color=GREY)
            elif b.type == "callout" and b.text.strip():
                _callout(doc, b.text, CORAL)
            elif b.type == "quote" and b.text.strip():
                _callout(doc, f"“{b.text}”", TEAL)
            elif b.type == "image" and (si, bi) in images:
                doc.add_picture(io.BytesIO(images[(si, bi)]), width=Mm(165))
                if b.caption:
                    add_inline(doc.add_paragraph(), b.caption, size=8.5, color=GREY)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
