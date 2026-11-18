"""Apply a model-produced EditPlan to the user's original file, keeping its formatting."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from docspec import EditPlan

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json"}
EDITABLE_EXTENSIONS = TEXT_EXTENSIONS | {".docx", ".pptx", ".xlsx"}

MIME = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf": "application/pdf",
}


@dataclass
class EditResult:
    data: bytes
    applied: int = 0
    warnings: list[str] = field(default_factory=list)


def extension(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:].lower() if dot >= 0 else ""


def edited_name(name: str) -> str:
    ext = extension(name)
    stem = name[: -len(ext)] if ext else name
    stem = re.sub(r"[\s_-]*\(?edited\)?$", "", stem, flags=re.I)
    return f"{stem} (edited){ext}"


def _replace_in_paragraph(paragraph, find: str, replace: str) -> bool:
    """python-docx and python-pptx paragraphs share .runs and run.text."""
    runs = list(paragraph.runs)
    for run in runs:
        if find in run.text:
            run.text = run.text.replace(find, replace)
            return True
    full = "".join(r.text for r in runs)
    if find not in full or not runs:
        return False
    runs[0].text = full.replace(find, replace)
    for r in runs[1:]:
        r.text = ""
    return True


def _apply_text_edits(paragraphs, plan: EditPlan, warnings: list[str]) -> int:
    paragraphs = list(paragraphs)
    applied = 0
    for edit in plan.replacements:
        if not edit.find:
            continue
        hit = False
        for p in paragraphs:
            if edit.find in "".join(r.text for r in p.runs) and _replace_in_paragraph(p, edit.find, edit.replace):
                hit = True
        if hit:
            applied += 1
        else:
            warnings.append(f"Text not found, skipped: “{edit.find[:60]}”")
    return applied


def _docx_paragraphs(doc):
    yield from doc.paragraphs
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs
    for section in doc.sections:
        for part in (section.header, section.footer):
            yield from part.paragraphs


def _pptx_paragraphs(prs):
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                yield from shape.text_frame.paragraphs
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    for cell in row.cells:
                        yield from cell.text_frame.paragraphs


def apply_edit(name: str, data: bytes, plan: EditPlan) -> EditResult:
    ext = extension(name)
    warnings: list[str] = []

    if ext in TEXT_EXTENSIONS:
        if plan.full_text.strip():
            return EditResult(plan.full_text.encode("utf-8"), applied=1)
        text = data.decode("utf-8", errors="replace")
        applied = 0
        for e in plan.replacements:
            if e.find and e.find in text:
                text = text.replace(e.find, e.replace)
                applied += 1
            elif e.find:
                warnings.append(f"Text not found, skipped: “{e.find[:60]}”")
        if plan.append_paragraphs:
            text = text.rstrip("\n") + "\n\n" + "\n\n".join(plan.append_paragraphs) + "\n"
            applied += len(plan.append_paragraphs)
        return EditResult(text.encode("utf-8"), applied, warnings)

    if ext == ".docx":
        from docx import Document

        doc = Document(io.BytesIO(data))
        applied = _apply_text_edits(_docx_paragraphs(doc), plan, warnings)
        for para in plan.append_paragraphs:
            doc.add_paragraph(para)
            applied += 1
        buf = io.BytesIO()
        doc.save(buf)
        return EditResult(buf.getvalue(), applied, warnings)

    if ext == ".pptx":
        from pptx import Presentation

        prs = Presentation(io.BytesIO(data))
        applied = _apply_text_edits(_pptx_paragraphs(prs), plan, warnings)
        if plan.append_paragraphs:
            warnings.append("New paragraphs are not added to presentations; use a new deck instead.")
        buf = io.BytesIO()
        prs.save(buf)
        return EditResult(buf.getvalue(), applied, warnings)

    if ext == ".xlsx":
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data))
        applied = 0
        for e in plan.cell_edits:
            ws = wb[e.sheet] if e.sheet in wb.sheetnames else wb.active
            value: object = e.value
            try:
                value = float(e.value) if "." in e.value else int(e.value)
            except ValueError:
                pass
            try:
                ws[e.cell] = value
                applied += 1
            except Exception:
                warnings.append(f"Invalid cell {e.cell}, skipped")
        for e in plan.replacements:
            hit = False
            for ws in wb.worksheets:
                for row in ws.iter_rows():
                    for cell in row:
                        if isinstance(cell.value, str) and e.find and e.find in cell.value:
                            cell.value = cell.value.replace(e.find, e.replace)
                            hit = True
            applied += int(hit)
        buf = io.BytesIO()
        wb.save(buf)
        return EditResult(buf.getvalue(), applied, warnings)

    raise ValueError(f"Editing {ext or 'this file type'} is not supported")
