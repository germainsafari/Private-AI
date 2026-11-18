"""Render a ReportSpec into an Admind-branded PDF (cover, contents, sections, tables, charts, images)."""

from __future__ import annotations

import html
import io
import re
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    Image,
    KeepTogether,
    ListFlowable,
    ListItem,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

from brand import (
    BLACK,
    CORAL,
    CREAM,
    CREAM_LIGHT,
    GREY,
    HAND_TEAL,
    LIGHT_GREY,
    LOGO,
    PETROL,
    SPHERE_TEAL,
    TEAL,
    pdf_fonts,
)
from charts import chart_drawing
from docspec import Block, ReportSpec

PAGE_W, PAGE_H = A4
MARGIN = 20 * mm
CONTENT_W = PAGE_W - 2 * MARGIN


def _c(hex_: str):
    return colors.HexColor("#" + hex_)


def inline(text: str) -> str:
    """Escape for reportlab Paragraph and convert **bold** / *italic* markdown."""
    t = html.escape(text or "", quote=False)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<i>\1</i>", t)
    return t.replace("\n", "<br/>")


def _styles() -> dict[str, ParagraphStyle]:
    f = pdf_fonts()
    base = ParagraphStyle("body", fontName=f["regular"], fontSize=10.5, leading=15, textColor=_c(BLACK))
    return {
        "body": base,
        "cover_title": ParagraphStyle("ct", parent=base, fontName=f["bold"], fontSize=34, leading=40),
        "cover_sub": ParagraphStyle("cs", parent=base, fontName=f["light"], fontSize=15, leading=21),
        "cover_meta": ParagraphStyle("cm", parent=base, fontSize=10, textColor=_c(GREY)),
        "h1": ParagraphStyle(
            "h1", parent=base, fontName=f["bold"], fontSize=21, leading=26, spaceBefore=6, spaceAfter=10
        ),
        "h2": ParagraphStyle(
            "h2", parent=base, fontName=f["bold"], fontSize=13.5, leading=18, spaceBefore=10, spaceAfter=4,
            textColor=_c(PETROL),
        ),
        "kicker": ParagraphStyle(
            "k", parent=base, fontName=f["bold"], fontSize=8.5, leading=11, textColor=_c(GREY), spaceAfter=2
        ),
        "p": ParagraphStyle("p", parent=base, spaceAfter=7, alignment=TA_LEFT),
        "li": ParagraphStyle("li", parent=base, spaceAfter=2),
        "cell": ParagraphStyle("cell", parent=base, fontSize=9, leading=12),
        "cell_head": ParagraphStyle("ch", parent=base, fontName=f["bold"], fontSize=9, leading=12),
        "caption": ParagraphStyle(
            "cap", parent=base, fontName=f["italic"], fontSize=8.5, leading=11, textColor=_c(GREY), spaceAfter=10
        ),
        "callout": ParagraphStyle("co", parent=base, fontSize=11, leading=16),
        "quote": ParagraphStyle("q", parent=base, fontName=f["light"], fontSize=14, leading=20),
        "toc1": ParagraphStyle("toc1", parent=base, fontSize=11, leading=18),
        "summary": ParagraphStyle("sum", parent=base, fontSize=11, leading=16, spaceAfter=5),
    }


def _title_markup(text: str) -> str:
    return f'{inline(text.rstrip("_ "))}<font color="#{CORAL}">_</font>'


class _Doc(BaseDocTemplate):
    def __init__(self, buf, spec: ReportSpec, **kw):
        super().__init__(buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                         topMargin=MARGIN + 8 * mm, bottomMargin=MARGIN, title=spec.title,
                         author="Admind", subject=spec.subtitle, **kw)
        self.spec = spec
        self.fonts = pdf_fonts()
        cover = Frame(MARGIN, MARGIN, CONTENT_W, PAGE_H - 2 * MARGIN, id="cover")
        body = Frame(MARGIN, MARGIN + 6 * mm, CONTENT_W, PAGE_H - 2 * MARGIN - 14 * mm, id="body")
        self.addPageTemplates([
            PageTemplate(id="Cover", frames=[cover], onPage=self._cover_bg),
            PageTemplate(id="Body", frames=[body], onPage=self._body_chrome),
        ])

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and getattr(flowable, "_toc_level", None) is not None:
            text = getattr(flowable, "_toc_text", flowable.getPlainText())
            key = f"sec-{self.seq.nextf('toc')}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=0, closed=True)
            self.notify("TOCEntry", (flowable._toc_level, text, self.page, key))

    def _cover_bg(self, canv, _doc):
        canv.saveState()
        canv.setFillColor(_c(CREAM))
        canv.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
        if SPHERE_TEAL.is_file():
            canv.drawImage(ImageReader(str(SPHERE_TEAL)), PAGE_W - 105 * mm, -25 * mm, 130 * mm, 130 * mm, mask="auto")
        if HAND_TEAL.is_file():
            canv.drawImage(ImageReader(str(HAND_TEAL)), MARGIN + 6 * mm, -12 * mm, 42 * mm, 97 * mm, mask="auto")
        if LOGO.is_file():
            canv.drawImage(ImageReader(str(LOGO)), MARGIN, PAGE_H - MARGIN - 14 * mm, 16 * mm, 14 * mm, mask="auto")
        canv.restoreState()

    def _body_chrome(self, canv, _doc):
        canv.saveState()
        top = PAGE_H - MARGIN + 2 * mm
        if LOGO.is_file():
            canv.drawImage(ImageReader(str(LOGO)), MARGIN, top - 1 * mm, 7 * mm, 6 * mm, mask="auto")
        canv.setFont(self.fonts["regular"], 8)
        canv.setFillColor(_c(GREY))
        canv.drawRightString(PAGE_W - MARGIN, top + 1 * mm, self.spec.title[:90])
        canv.setStrokeColor(_c(LIGHT_GREY))
        canv.setLineWidth(0.4)
        canv.line(MARGIN, top - 2.5 * mm, PAGE_W - MARGIN, top - 2.5 * mm)
        canv.setFillColor(_c(CORAL))
        canv.rect(MARGIN, MARGIN - 4 * mm, 6 * mm, 0.9 * mm, stroke=0, fill=1)
        canv.setFillColor(_c(GREY))
        canv.drawRightString(PAGE_W - MARGIN, MARGIN - 5 * mm, str(canv.getPageNumber()))
        canv.restoreState()


def _table(block: Block, st) -> Table | None:
    if not block.headers and not block.rows:
        return None
    ncols = max([len(block.headers)] + [len(r) for r in block.rows]) or 1
    rows = []
    if block.headers:
        rows.append([Paragraph(inline(h), st["cell_head"]) for h in block.headers] +
                    [""] * (ncols - len(block.headers)))
    for r in block.rows[:400]:
        rows.append([Paragraph(inline(str(c)), st["cell"]) for c in r] + [""] * (ncols - len(r)))
    t = Table(rows, colWidths=[CONTENT_W / ncols] * ncols, repeatRows=1 if block.headers else 0, hAlign="LEFT")
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, _c(LIGHT_GREY)),
    ]
    if block.headers:
        style += [("BACKGROUND", (0, 0), (-1, 0), _c(TEAL)), ("LINEBELOW", (0, 0), (-1, 0), 0.8, _c(BLACK))]
    start = 1 if block.headers else 0
    for i in range(start, len(rows)):
        if (i - start) % 2 == 1:
            style.append(("BACKGROUND", (0, i), (-1, i), _c(CREAM_LIGHT)))
    t.setStyle(TableStyle(style))
    return t


def _boxed(flowable, bar=CORAL, bg=CREAM_LIGHT) -> Table:
    t = Table([[flowable]], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _c(bg)),
        ("LINEBEFORE", (0, 0), (0, -1), 3, _c(bar)),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    return t


def _image(data: bytes, caption: str, st) -> list:
    reader = ImageReader(io.BytesIO(data))
    iw, ih = reader.getSize()
    w = CONTENT_W
    h = w * ih / iw
    max_h = 115 * mm
    if h > max_h:
        h, w = max_h, max_h * iw / ih
    out = [Image(io.BytesIO(data), width=w, height=h, hAlign="CENTER")]
    if caption:
        out.append(Paragraph(inline(caption), st["caption"]))
    return [KeepTogether(out), Spacer(1, 4)]


def _blocks(si: int, blocks: list[Block], st, images: dict[tuple[int, int], bytes]) -> list:
    flow: list = []
    for bi, b in enumerate(blocks):
        if b.type == "paragraph" and b.text.strip():
            flow.append(Paragraph(inline(b.text), st["p"]))
        elif b.type == "subheading" and b.text.strip():
            flow.append(CondPageBreak(30 * mm))
            flow.append(Paragraph(inline(b.text), st["h2"]))
        elif b.type in ("bullets", "numbered") and b.items:
            items = [ListItem(Paragraph(inline(i), st["li"]), leftIndent=14,
                              value=None if b.type == "bullets" else n + 1)
                     for n, i in enumerate(b.items)]
            flow.append(ListFlowable(
                items, bulletType="bullet" if b.type == "bullets" else "1",
                start="•" if b.type == "bullets" else 1, bulletColor=_c(CORAL),
                bulletFontName=st["body"].fontName, leftIndent=14, bulletFontSize=10,
            ))
            flow.append(Spacer(1, 6))
        elif b.type == "table":
            t = _table(b, st)
            if t is not None:
                flow += [t, Spacer(1, 4)]
                if b.caption:
                    flow.append(Paragraph(inline(b.caption), st["caption"]))
                else:
                    flow.append(Spacer(1, 6))
        elif b.type == "chart" and b.chart:
            d = chart_drawing(b.chart, width=CONTENT_W, height=72 * mm)
            if d is not None:
                parts = [d]
                if b.caption:
                    parts.append(Paragraph(inline(b.caption), st["caption"]))
                flow += [KeepTogether(parts), Spacer(1, 8)]
        elif b.type == "callout" and b.text.strip():
            flow += [_boxed(Paragraph(inline(b.text), st["callout"])), Spacer(1, 10)]
        elif b.type == "quote" and b.text.strip():
            q = Paragraph(f'<font color="#{CORAL}" size="22">“</font> {inline(b.text)}', st["quote"])
            flow += [_boxed(q, bar=TEAL, bg="FFFFFF"), Spacer(1, 10)]
        elif b.type == "image" and (si, bi) in images:
            flow += _image(images[(si, bi)], b.caption, st)
    return flow


def render_pdf(spec: ReportSpec, images: dict[tuple[int, int], bytes] | None = None) -> bytes:
    """images maps (section index, block index) → bytes for image blocks that were generated."""
    images = images or {}
    st = _styles()
    buf = io.BytesIO()
    doc = _Doc(buf, spec)
    story: list = []

    story.append(Spacer(1, 70 * mm))
    story.append(Paragraph(_title_markup(spec.title), st["cover_title"]))
    story.append(Spacer(1, 6 * mm))
    if spec.subtitle:
        story.append(Paragraph(inline(spec.subtitle), st["cover_sub"]))
        story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(inline(spec.date or date.today().strftime("%d %B %Y")), st["cover_meta"]))
    story.append(NextPageTemplate("Body"))
    story.append(PageBreak())

    if len(spec.sections) >= 3:
        toc = TableOfContents()
        toc.levelStyles = [st["toc1"]]
        toc.dotsMinLevel = 0
        story.append(Paragraph(_title_markup("Contents"), st["h1"]))
        story.append(toc)
        story.append(PageBreak())

    if spec.executive_summary:
        h = Paragraph(_title_markup("Executive summary"), st["h1"])
        h._toc_level, h._toc_text = 0, "Executive summary"
        story.append(h)
        items = [ListItem(Paragraph(inline(s), st["summary"]), leftIndent=14) for s in spec.executive_summary]
        story.append(_boxed(ListFlowable(items, bulletType="bullet", start="•", bulletColor=_c(CORAL),
                                         leftIndent=14), bar=TEAL))
        story.append(Spacer(1, 10 * mm))

    for si, sec in enumerate(spec.sections):
        story.append(CondPageBreak(70 * mm))
        story.append(Paragraph(f"SECTION {si + 1:02d}", st["kicker"]))
        h = Paragraph(_title_markup(sec.heading), st["h1"])
        h._toc_level, h._toc_text = 0, sec.heading
        story.append(h)
        story += _blocks(si, sec.blocks, st, images)
        story.append(Spacer(1, 6 * mm))

    doc.multiBuild(story)
    return buf.getvalue()
