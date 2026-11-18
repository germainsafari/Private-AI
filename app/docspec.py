"""Structured contracts between the model and the file renderers."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Action = Literal["chat", "pdf_report", "docx_document", "presentation", "edit_file", "image"]


class Intent(BaseModel):
    action: Action
    title: str = Field(description="Short working title for the file, empty for chat")
    target_pages: int = Field(description="Requested length in pages for reports, 0 if not stated")
    slide_count: int = Field(description="Requested number of slides, 0 if not stated")
    wants_images: bool = Field(description="True only if the user explicitly asked for images or illustrations")
    language: str = Field(description="Language the file should be written in, e.g. English or Polish")


class SectionPlan(BaseModel):
    heading: str
    brief: str = Field(description="What this section must cover, including facts from the sources")
    target_words: int
    visual: Literal["none", "table", "chart", "image"]


class ReportOutline(BaseModel):
    title: str
    subtitle: str
    executive_summary: list[str] = Field(description="3-6 key takeaways, one sentence each")
    sections: list[SectionPlan]


class ChartSeries(BaseModel):
    name: str
    values: list[float]


class Chart(BaseModel):
    kind: Literal["bar", "line", "pie"]
    title: str
    categories: list[str]
    series: list[ChartSeries]
    unit: str = ""


class Block(BaseModel):
    type: Literal[
        "paragraph", "subheading", "bullets", "numbered", "table", "chart", "callout", "quote", "image"
    ]
    text: str = ""
    items: list[str] = []
    headers: list[str] = []
    rows: list[list[str]] = []
    chart: Optional[Chart] = None
    image_prompt: str = ""
    caption: str = ""


class SectionContent(BaseModel):
    heading: str
    blocks: list[Block]


class ReportSpec(BaseModel):
    title: str
    subtitle: str = ""
    date: str = ""
    executive_summary: list[str] = []
    sections: list[SectionContent] = []


class TextEdit(BaseModel):
    find: str = Field(description="Exact text copied from the document")
    replace: str


class CellEdit(BaseModel):
    sheet: str
    cell: str = Field(description="A1-style reference")
    value: str


class EditPlan(BaseModel):
    summary: str = Field(description="One or two sentences describing what was changed")
    replacements: list[TextEdit]
    append_paragraphs: list[str]
    cell_edits: list[CellEdit]
    full_text: str = Field(description="Complete new file content for plain-text formats, otherwise empty")


def spec_to_markdown(spec: ReportSpec) -> str:
    """Plain-text rendering kept by the browser so follow-up requests can revise the report."""
    out = [f"# {spec.title}"]
    if spec.subtitle:
        out.append(spec.subtitle)
    if spec.executive_summary:
        out.append("\n## Executive summary")
        out.extend(f"- {s}" for s in spec.executive_summary)
    for sec in spec.sections:
        out.append(f"\n## {sec.heading}")
        for b in sec.blocks:
            if b.type == "subheading":
                out.append(f"\n### {b.text}")
            elif b.type in ("paragraph", "callout", "quote"):
                out.append(b.text)
            elif b.type in ("bullets", "numbered"):
                out.extend(f"- {i}" for i in b.items)
            elif b.type == "table" and b.headers:
                out.append(" | ".join(b.headers))
                out.extend(" | ".join(r) for r in b.rows)
            elif b.type == "chart" and b.chart:
                out.append(f"[chart: {b.chart.title}]")
            elif b.type == "image":
                out.append(f"[image: {b.caption or b.image_prompt}]")
    return "\n".join(out)
