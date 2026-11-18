"""Offline sample report used by the renderer tests."""

from docspec import Block, Chart, ChartSeries, ReportSpec, SectionContent


def sample_spec(sections: int = 4) -> ReportSpec:
    secs = []
    for i in range(sections):
        secs.append(SectionContent(
            heading=f"Market review {i + 1} — zażółć gęślą jaźń",
            blocks=[
                Block(type="paragraph", text="Brand awareness grew **12 points** year on year, driven by *organic* reach. " * 6),
                Block(type="subheading", text="Key drivers"),
                Block(type="bullets", items=["Social video", "Partnerships", "Employer branding"]),
                Block(type="table", headers=["Metric", "2025", "2026"],
                      rows=[["Awareness", "41%", "53%"], ["Consideration", "22%", "27%"], ["NPS", "31", "38"]],
                      caption="Source: Admind brand tracker"),
                Block(type="chart", chart=Chart(kind="bar", title="Awareness by quarter", categories=["Q1", "Q2", "Q3", "Q4"],
                                                series=[ChartSeries(name="2025", values=[38, 40, 41, 44]),
                                                        ChartSeries(name="2026", values=[47, 49, 51, 53])], unit="%")),
                Block(type="callout", text="Recommendation: double down on video formats in Q1."),
                Block(type="quote", text="The brand finally feels like us."),
                Block(type="numbered", items=["Audit channels", "Reallocate budget", "Measure monthly"]),
            ],
        ))
    secs.append(SectionContent(heading="Mix", blocks=[
        Block(type="chart", chart=Chart(kind="pie", title="Budget split", categories=["Video", "Search", "OOH"],
                                        series=[ChartSeries(name="Share", values=[50, 30, 20])], unit="%")),
        Block(type="chart", chart=Chart(kind="line", title="Trend", categories=["Jan", "Feb", "Mar"],
                                        series=[ChartSeries(name="Leads", values=[10, 14, 19])])),
    ]))
    return ReportSpec(title="Brand Health Report 2026", subtitle="Survey results and recommendations",
                      executive_summary=["Awareness up 12 points.", "Video drives growth.", "NPS improved."],
                      sections=secs)
