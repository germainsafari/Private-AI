"""
Overlay throughput and latency curves from the three quantization CSVs
(fp16.csv, int8_bnb.csv, awq_int4.csv) onto one comparison chart.

Usage:
    python plot_quant_comparison.py results/quantization
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SERIES = [
    ("fp16.csv", "FP16 (baseline)", "#5a9bff"),
    ("int8_bnb.csv", "INT8 (bitsandbytes)", "#ffd24a"),
    ("awq_int4.csv", "INT4 (AWQ)", "#ff8a3d"),
]


def load(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: int(r["concurrency"]))
    return rows


def to_float(v):
    if v in (None, "", "None"):
        return None
    return float(v)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("results_dir", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    fig.suptitle("Quantization comparison: Qwen2.5-7B-Instruct")

    for fname, label, color in SERIES:
        path = args.results_dir / fname
        if not path.exists():
            continue
        rows = load(path)
        concurrency = [int(r["concurrency"]) for r in rows]
        throughput = [to_float(r["approx_tokens_per_sec"]) for r in rows]
        ttft_p50 = [to_float(r["ttft_p50_ms"]) for r in rows]

        axes[0].plot(concurrency, throughput, marker="o", label=label, color=color)
        axes[1].plot(concurrency, ttft_p50, marker="o", label=label, color=color)

    axes[0].set_xlabel("Concurrency")
    axes[0].set_ylabel("Approx. tokens/sec")
    axes[0].set_title("Throughput vs concurrency")
    axes[0].legend(fontsize=8)
    axes[0].grid(alpha=0.3)

    axes[1].set_xlabel("Concurrency")
    axes[1].set_ylabel("TTFT p50 (ms)")
    axes[1].set_title("TTFT p50 vs concurrency")
    axes[1].legend(fontsize=8)
    axes[1].grid(alpha=0.3)

    fig.tight_layout()
    out_path = args.out or (args.results_dir / "comparison.png")
    fig.savefig(out_path, dpi=140)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
