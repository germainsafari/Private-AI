"""Build docs/Inference_Engineering_Report.pdf from results tables and charts."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "Inference_Engineering_Report.pdf"
RESULTS = ROOT / "benchmarks" / "results"


def styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "T",
            parent=base["Title"],
            fontSize=22,
            spaceAfter=8,
            textColor=colors.HexColor("#0f172a"),
        ),
        "sub": ParagraphStyle(
            "S",
            parent=base["Normal"],
            fontSize=11,
            textColor=colors.HexColor("#475569"),
            alignment=TA_CENTER,
            spaceAfter=18,
        ),
        "h1": ParagraphStyle(
            "H1",
            parent=base["Heading1"],
            fontSize=14,
            spaceBefore=16,
            spaceAfter=8,
            textColor=colors.HexColor("#0f172a"),
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontSize=12,
            spaceBefore=12,
            spaceAfter=6,
            textColor=colors.HexColor("#1e293b"),
        ),
        "body": ParagraphStyle(
            "B",
            parent=base["Normal"],
            fontSize=9.5,
            leading=13,
            alignment=TA_JUSTIFY,
            spaceAfter=8,
        ),
        "caption": ParagraphStyle(
            "C",
            parent=base["Normal"],
            fontSize=8,
            textColor=colors.HexColor("#64748b"),
            alignment=TA_CENTER,
            spaceBefore=4,
            spaceAfter=12,
        ),
        "bullet": ParagraphStyle(
            "Bu",
            parent=base["Normal"],
            fontSize=9.5,
            leading=12,
            leftIndent=4,
        ),
        "footer": ParagraphStyle(
            "F",
            parent=base["Normal"],
            fontSize=8,
            textColor=colors.HexColor("#94a3b8"),
            alignment=TA_CENTER,
        ),
    }


def img(path: Path, width=6.3 * inch):
    if not path.is_file():
        return Paragraph(f"<i>[Missing figure: {path.name}]</i>", styles()["caption"])
    # Preserve aspect ratio from a reasonable max width
    im = Image(str(path))
    aspect = im.imageHeight / float(im.imageWidth)
    im.drawWidth = width
    im.drawHeight = width * aspect
    if im.drawHeight > 3.6 * inch:
        scale = (3.6 * inch) / im.drawHeight
        im.drawWidth *= scale
        im.drawHeight *= scale
    return im


def table(data, col_widths=None):
    t = Table(data, colWidths=col_widths, hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f8fafc")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f8fafc"), colors.white]),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return t


def bullets(items, st):
    return ListFlowable(
        [ListItem(Paragraph(i, st["bullet"]), leftIndent=8, value="•") for i in items],
        bulletType="bullet",
        start="•",
        leftIndent=12,
        spaceBefore=2,
        spaceAfter=8,
    )


def build():
    st = styles()
    story = []

    story.append(Paragraph("LLM Inference Engineering Platform", st["title"]))
    story.append(
        Paragraph(
            "High-level implementation, measured findings, and charts — "
            "Qwen2.5-VL / vLLM on NVIDIA RTX 5090 (32GB)",
            st["sub"],
        )
    )

    story.append(Paragraph("1. Overview", st["h1"]))
    story.append(
        Paragraph(
            "FastAPI gateway in front of a local vLLM OpenAI-compatible server (Qwen2.5-VL on "
            "Windows 11 + WSL2, RTX 5090). The gateway provides admission control, TTFT and latency "
            "metrics, GPU telemetry, a live dashboard, optional SLA-based multi-model routing, and "
            "benchmark scripts for concurrency, quantization, and prefill/decode timing.",
            st["body"],
        )
    )

    story.append(Paragraph("2. High-level architecture", st["h1"]))
    story.append(
        Paragraph(
            "<b>Browser</b> (chat UI + <b>/dashboard</b>) → <b>FastAPI gateway</b> "
            "(async proxy, orchestrator, metrics, router) → <b>vLLM</b> "
            "(continuous batching) → <b>RTX 5090</b>.",
            st["body"],
        )
    )
    story.append(Paragraph("Core modules", st["h2"]))
    story.append(
        bullets(
            [
                "<b>app/orchestrator.py</b> — bounded concurrency, queue depth, HTTP 429 backpressure, "
                "per-request tickets (queued / admitted / first token / completed).",
                "<b>app/metrics.py</b> — NVML GPU telemetry + rolling P50/P95/P99 for wait, TTFT, "
                "processing, total latency; tokens/sec.",
                "<b>app/router.py</b> — per-backend orchestrator + metrics; spill to fallback when "
                "p95 TTFT breaches SLA.",
                "<b>app/main.py</b> — AsyncOpenAI streaming chat, <b>/api/metrics</b>, "
                "<b>/api/metrics/stream</b>, <b>/api/router/status</b>, <b>/dashboard</b>.",
                "<b>benchmarks/</b> — load generator, plotting, router/quant/prefill probes; "
                "CSVs and PNGs committed under <b>benchmarks/results/</b>.",
            ],
            st,
        )
    )

    story.append(Paragraph("3. Crash under load", st["h1"]))
    story.append(
        Paragraph(
            "At concurrency ≥ 4 with default <b>--gpu-memory-utilization 0.9</b>, vLLM crashed "
            "and took down the WSL2 GPU VM. With <b>0.85</b> and gateway "
            "<b>MAX_CONCURRENCY=8</b>, the same sweep completed through concurrency 32 with no "
            "errors. Overload then showed up as queue wait rather than a process crash.",
            st["body"],
        )
    )

    story.append(Paragraph("4. Text concurrency sweep (through the gateway)", st["h1"]))
    story.append(
        table(
            [
                ["Concurrency", "Throughput (tok/s)", "TTFT p50", "TTFT p95", "Total p50", "GPU util"],
                ["1", "138.3", "66.8 ms", "104.7 ms", "2059 ms", "98%"],
                ["4", "547.1", "80.7 ms", "83.3 ms", "2095 ms", "98%"],
                ["8", "1043.2", "82.3 ms", "91.0 ms", "2098 ms", "98%"],
                ["16", "1043.1", "843.4 ms", "2203 ms", "2867 ms", "98%"],
            ],
            col_widths=[0.95 * inch, 1.25 * inch, 0.95 * inch, 0.95 * inch, 0.95 * inch, 0.8 * inch],
        )
    )
    story.append(Spacer(1, 8))
    story.append(
        KeepTogether(
            [
                img(RESULTS / "text_sweep.png"),
                Paragraph("Figure 1 — Text concurrency: throughput vs latency percentiles", st["caption"]),
            ]
        )
    )
    story.append(
        Paragraph(
            "Throughput scales roughly linearly from concurrency 1→8, then plateaus at 16 while "
            "TTFT rises (queue wait). GPU stays ~98%. The plateau matches the gateway "
            "MAX_CONCURRENCY=8 limit.",
            st["body"],
        )
    )

    story.append(Paragraph("5. Vision and long-prompt sweeps", st["h1"]))
    story.append(Paragraph("Vision (Qwen2.5-VL, one image + prompt)", st["h2"]))
    story.append(
        table(
            [
                ["Concurrency", "Throughput (tok/s)", "TTFT p50", "Total p50"],
                ["1", "107.1", "26.7 ms", "1541 ms"],
                ["4", "434.2", "47.5 ms", "1582 ms"],
            ]
        )
    )
    story.append(Spacer(1, 6))
    story.append(
        KeepTogether(
            [
                img(RESULTS / "vision_sweep.png"),
                Paragraph("Figure 2 — Vision concurrency sweep", st["caption"]),
            ]
        )
    )

    story.append(Paragraph("Long prompt (~2,400 input tokens)", st["h2"]))
    story.append(
        table(
            [
                ["Concurrency", "Throughput (tok/s)", "TTFT p50", "Total p50"],
                ["1", "142.1", "24.4 ms", "1530 ms"],
                ["4", "458.8", "37.8 ms", "1569 ms"],
                ["8", "695.8", "60.7 ms", "1627 ms"],
            ]
        )
    )
    story.append(Spacer(1, 6))
    story.append(
        KeepTogether(
            [
                img(RESULTS / "long_prompt_sweep.png"),
                Paragraph("Figure 3 — Long-prompt concurrency sweep", st["caption"]),
            ]
        )
    )
    story.append(
        Paragraph(
            "Even with ~8× more input tokens, TTFT stays under ~61 ms at concurrency 8 — prefill "
            "is fast enough on this GPU that decode (memory-bandwidth-bound) still dominates total latency.",
            st["body"],
        )
    )

    story.append(PageBreak())
    story.append(Paragraph("6. Multi-model SLA routing", st["h1"]))
    story.append(
        Paragraph(
            "Each logical backend has its own orchestrator and metrics. When primary p95 TTFT "
            "exceeds its SLA, new requests go to <b>fast</b>. Two vLLM engines on this single-GPU "
            "WSL2 host crashed the VM, so the load test used two logical backends against one "
            "physical engine (same routing code; different URLs on a multi-engine host).",
            st["body"],
        )
    )
    story.append(
        table(
            [
                ["Backend", "Requests routed", "TTFT p50", "TTFT p95", "Breaching SLA?"],
                ["primary (capped)", "10", "2396 ms", "3867 ms", "yes"],
                ["fast (fallback)", "30", "80.8 ms", "464 ms", "no"],
            ]
        )
    )
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            "After enough TTFT samples, later requests went to <b>fast</b>. Zero errors in 40 requests.",
            st["body"],
        )
    )

    story.append(Paragraph("7. Quantization: FP16 vs INT8 (bitsandbytes) vs INT4 (AWQ)", st["h1"]))
    story.append(
        Paragraph(
            "Same model family (<b>Qwen2.5-7B-Instruct</b>), same prompts/gateway settings; only "
            "weight format changes. Variants loaded one-at-a-time (VRAM / WSL stability).",
            st["body"],
        )
    )
    story.append(
        table(
            [
                ["Variant", "c=1 tok/s", "c=4 tok/s", "c=8 tok/s", "TTFT p50 (c=4)", "Notes"],
                ["FP16", "131.7", "482.6", "390.0", "83.9 ms", "Baseline"],
                ["INT8 (bnb)", "219.9", "285.9", "272.7", "97.6 ms", "Memory tool, not speed win"],
                ["INT4 (AWQ)", "93.8*", "1080.0", "579.4", "64.4 ms", "Best latency + peak tput"],
            ],
            col_widths=[1.05 * inch, 0.75 * inch, 0.75 * inch, 0.75 * inch, 1.05 * inch, 1.6 * inch],
        )
    )
    story.append(
        Paragraph(
            "* AWQ c=1 throughput includes a one-time JIT/kernel warm-up (~9 s first request); "
            "steady-state TTFT ~60 ms.",
            st["caption"],
        )
    )
    story.append(
        KeepTogether(
            [
                img(RESULTS / "quantization" / "comparison.png"),
                Paragraph("Figure 4 — Quantization comparison", st["caption"]),
            ]
        )
    )
    story.append(
        Paragraph(
            "On this GPU, AWQ INT4 had the best latency and peak throughput. bitsandbytes INT8 "
            "reduces VRAM vs FP16 but did not improve tokens/sec here.",
            st["body"],
        )
    )

    story.append(Paragraph("8. Prefill / decode asymmetry (disaggregation motivation)", st["h1"]))
    story.append(
        Paragraph(
            "Prefill is compute-bound and grows with prompt length; decode is memory-bandwidth-bound "
            "with roughly constant per-token cost. Full two-process disaggregation (vLLM "
            "<b>kv_transfer_config</b>) wants separate GPUs; this host has one, and dual engines "
            "already crashed WSL2 here. Below is the measured cost split only.",
            st["body"],
        )
    )
    story.append(
        table(
            [
                ["Prompt tokens", "Prefill (ms)", "Decode ms / token"],
                ["300", "24.1", "15.3"],
                ["800", "30.3", "13.9"],
                ["1800", "41.5", "15.3"],
                ["3000", "44.9", "15.0"],
            ]
        )
    )
    story.append(Spacer(1, 6))
    story.append(
        KeepTogether(
            [
                img(RESULTS / "prefill_decode.png"),
                Paragraph("Figure 5 — Prefill time vs per-token decode cost by prompt length", st["caption"]),
            ]
        )
    )

    story.append(Paragraph("9. Observability surface", st["h1"]))
    story.append(
        bullets(
            [
                "<b>GET /api/metrics</b> — GPU util/VRAM/power, queue depth, active requests, tok/s, percentiles.",
                "<b>GET /api/metrics/stream</b> — same payload over SSE (~1 Hz) for the dashboard.",
                "<b>GET /dashboard</b> — live Chart.js UI.",
                "Streaming chat <b>done</b> events include <b>ttft_ms</b>, backend name, and degraded flag.",
            ],
            st,
        )
    )

    story.append(Paragraph("10. Summary", st["h1"]))
    story.append(
        bullets(
            [
                "GPU memory headroom and gateway concurrency limits avoided the crash path under load.",
                "Throughput scales with concurrency until the admission ceiling; beyond that, queue wait grows while tok/s stays flat.",
                "Separate wait vs processing vs TTFT in metrics; SLA routing can spill to a fallback backend.",
                "AWQ INT4 beat FP16 and bitsandbytes INT8 on latency and peak throughput in these runs.",
                "Prefill cost grows with prompt length; decode ms/token stays roughly flat — motivating split serving when multiple GPUs are available.",
            ],
            st,
        )
    )

    story.append(Spacer(1, 16))
    story.append(
        Paragraph(
            "Data: benchmarks/results/, RESULTS.md, router/RESULTS.md, "
            "quantization/RESULTS.md, disaggregation/RESULTS.md.",
            st["footer"],
        )
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=A4,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch,
        title="LLM Inference Engineering Platform — Findings",
        author="Germain Safari",
    )
    doc.build(story)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    build()
