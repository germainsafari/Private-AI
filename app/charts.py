"""Data charts drawn with reportlab graphics in Admind colours; PNG export via PyMuPDF for Word files."""

from __future__ import annotations

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors

from brand import BLACK, GREY, LIGHT_GREY, SERIES_COLORS, pdf_fonts
from docspec import Chart


def _c(hex_: str):
    return colors.HexColor("#" + hex_)


def _clean(chart: Chart) -> tuple[list[str], list[tuple[str, list[float]]]]:
    cats = [str(c)[:28] for c in chart.categories][:24]
    series = []
    for s in chart.series[:6]:
        vals = [float(v) for v in s.values][: len(cats)]
        vals += [0.0] * (len(cats) - len(vals))
        series.append((s.name[:40], vals))
    return cats, series


def chart_drawing(chart: Chart, width: float = 440, height: float = 250) -> Drawing | None:
    cats, series = _clean(chart)
    if not cats or not series:
        return None
    f = pdf_fonts()
    d = Drawing(width, height)
    d.add(String(0, height - 14, chart.title[:90], fontName=f["bold"], fontSize=11, fillColor=_c(BLACK)))
    plot_h = height - 70
    unit = f" ({chart.unit})" if chart.unit else ""

    if chart.kind == "pie":
        pie = Pie()
        pie.x, pie.y = 20, 20
        pie.width = pie.height = min(plot_h, width / 2 - 20)
        pie.data = [max(0.0, v) for v in series[0][1]] or [1]
        pie.labels = None
        pie.slices.strokeColor = colors.white
        pie.slices.strokeWidth = 1
        for i in range(len(pie.data)):
            pie.slices[i].fillColor = _c(SERIES_COLORS[i % len(SERIES_COLORS)])
        d.add(pie)
        legend = Legend()
        legend.x, legend.y = pie.x + pie.width + 30, pie.y + pie.height
        legend.fontName, legend.fontSize = f["regular"], 9
        legend.alignment = "right"
        legend.colorNamePairs = [
            (_c(SERIES_COLORS[i % len(SERIES_COLORS)]), f"{c}: {v:g}{chart.unit}")
            for i, (c, v) in enumerate(zip(cats, series[0][1]))
        ]
        d.add(legend)
        return d

    plot = VerticalBarChart() if chart.kind == "bar" else HorizontalLineChart()
    plot.x, plot.y = 45, 45
    plot.width, plot.height = width - 60, plot_h - 22
    plot.data = [vals for _, vals in series]
    plot.categoryAxis.categoryNames = cats
    plot.categoryAxis.labels.fontName = f["regular"]
    plot.categoryAxis.labels.fontSize = 8
    plot.categoryAxis.labels.fillColor = _c(GREY)
    if len(cats) > 6:
        plot.categoryAxis.labels.angle = 30
        plot.categoryAxis.labels.boxAnchor = "ne"
    plot.valueAxis.labels.fontName = f["regular"]
    plot.valueAxis.labels.fontSize = 8
    plot.valueAxis.labels.fillColor = _c(GREY)
    plot.valueAxis.gridStrokeColor = _c(LIGHT_GREY)
    plot.valueAxis.visibleGrid = True
    all_vals = [v for _, vals in series for v in vals]
    plot.valueAxis.valueMin = min(0.0, min(all_vals))
    for i in range(len(series)):
        color = _c(SERIES_COLORS[i % len(SERIES_COLORS)])
        if chart.kind == "bar":
            plot.bars[i].fillColor = color
            plot.bars[i].strokeColor = None
        else:
            plot.lines[i].strokeColor = color
            plot.lines[i].strokeWidth = 2.2
    if chart.kind == "bar":
        plot.barSpacing = 1
        plot.groupSpacing = 8
    d.add(plot)

    if len(series) > 1 or unit:
        legend = Legend()
        legend.x, legend.y = 45, height - 26
        legend.fontName, legend.fontSize = f["regular"], 9
        legend.columnMaximum = 1
        legend.deltax = 110
        legend.alignment = "right"
        legend.colorNamePairs = [
            (_c(SERIES_COLORS[i % len(SERIES_COLORS)]), f"{name}{unit}") for i, (name, _) in enumerate(series)
        ]
        d.add(legend)
    return d


def chart_png(chart: Chart, scale: float = 2.5) -> bytes | None:
    d = chart_drawing(chart)
    if d is None:
        return None
    import fitz
    from reportlab.graphics import renderPDF

    pdf = renderPDF.drawToString(d)
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        return pix.tobytes("png")
    finally:
        doc.close()
