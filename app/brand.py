"""Admind visual identity shared by the PDF and DOCX renderers (palette matches the deck engine)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
BRAND_DIR = ASSETS_DIR / "brand"

CREAM = "EDD9C2"
CREAM_LIGHT = "F6EDE2"
TEAL = "29D9C2"
TEAL_TEXT = "1FA896"
GREEN = "006C5B"
PETROL = "00505A"
CORAL = "FF523D"
ORANGE = "E5734A"
PEACH = "F7B273"
PINK = "ED8787"
BLACK = "000000"
GREY = "5F5F5F"
LIGHT_GREY = "C8C8C8"
WHITE = "FFFFFF"

SERIES_COLORS = [TEAL, CORAL, GREEN, PEACH, PINK, PETROL]

# Word documents reference the licensed brand font; machines without it substitute.
DOCX_FONT = "Acumin Pro"

PDF_FONT = "SourceSans3"
PDF_FONT_BOLD = "SourceSans3-Bold"
PDF_FONT_LIGHT = "SourceSans3-Light"
PDF_FONT_ITALIC = "SourceSans3-Italic"

LOGO = BRAND_DIR / "illustration_logo_a.png"
SPHERE_TEAL = BRAND_DIR / "sphere_teal.png"
SPHERE_CREAM = BRAND_DIR / "sphere_cream.png"
HAND_TEAL = BRAND_DIR / "hand_thumb_teal.png"

IMAGE_STYLE_HINT = (
    "Flat editorial illustration in the Admind brand style: warm cream background (#EDD9C2), "
    "teal (#29D9C2) and coral (#FF523D) shapes, thin black line details, generous negative space, "
    "no text, no logos, no watermarks."
)


def hex_rgb(hex_: str) -> tuple[int, int, int]:
    return int(hex_[0:2], 16), int(hex_[2:4], 16), int(hex_[4:6], 16)


@lru_cache
def register_pdf_fonts() -> bool:
    """Embed Source Sans 3 (OFL) so Polish and other Latin Extended text renders; False → Helvetica."""
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    files = {
        PDF_FONT: "SourceSans3-Regular.ttf",
        PDF_FONT_BOLD: "SourceSans3-Bold.ttf",
        PDF_FONT_LIGHT: "SourceSans3-Light.ttf",
        PDF_FONT_ITALIC: "SourceSans3-It.ttf",
    }
    try:
        for name, file in files.items():
            pdfmetrics.registerFont(TTFont(name, str(FONTS_DIR / file)))
    except Exception:
        return False
    addMapping(PDF_FONT, 0, 0, PDF_FONT)
    addMapping(PDF_FONT, 1, 0, PDF_FONT_BOLD)
    addMapping(PDF_FONT, 0, 1, PDF_FONT_ITALIC)
    addMapping(PDF_FONT, 1, 1, PDF_FONT_BOLD)
    return True


def pdf_fonts() -> dict[str, str]:
    if register_pdf_fonts():
        return {
            "regular": PDF_FONT,
            "bold": PDF_FONT_BOLD,
            "light": PDF_FONT_LIGHT,
            "italic": PDF_FONT_ITALIC,
        }
    return {
        "regular": "Helvetica",
        "bold": "Helvetica-Bold",
        "light": "Helvetica",
        "italic": "Helvetica-Oblique",
    }
