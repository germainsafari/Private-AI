"""
Render charts from a bench_client.py CSV: throughput vs concurrency and
latency percentiles vs concurrency.

Usage:
    python plot_results.py results/sweep.csv --out results/sweep.png
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def load_rows(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def to_float(v: str | None) -> float | None:
    if v in (None, "", "None"):
        return None
    return float(v)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--title", default="Concurrency sweep")
    args = parser.parse_args()

    rows = load_rows(args.csv_path)
    rows.sort(key=lambda r: int(r["concurrency"]))

    concurrency = [int(r["concurrency"]) for r in rows]
    throughput = [to_float(r["approx_tokens_per_sec"]) for r in rows]
    ttft_p50 = [to_float(r["ttft_p50_ms"]) for r in rows]
    ttft_p95 = [to_float(r["ttft_p95_ms"]) for r in rows]
    total_p50 = [to_float(r["total_p50_ms"]) for r in rows]
    total_p95 = [to_float(r["total_p95_ms"]) for r in rows]
    gpu_before = [to_float(r["gpu_util_before"]) for r in rows]
    gpu_after = [to_float(r["gpu_util_after"]) for r in rows]

    fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))
    fig.suptitle(args.title)

    ax = axes[0]
    ax.plot(concurrency, throughput, marker="o", color="#ff8a3d")
    ax.set_xlabel("Concurrency")
    ax.set_ylabel("Approx. tokens/sec")
    ax.set_title("Throughput vs concurrency")
    ax.grid(alpha=0.3)

    ax = axes[1]
    ax.plot(concurrency, ttft_p50, marker="o", label="TTFT p50", color="#5a9bff")
    ax.plot(concurrency, ttft_p95, marker="o", label="TTFT p95", color="#5a9bff", linestyle="--")
    ax.plot(concurrency, total_p50, marker="s", label="Total p50", color="#ffd24a")
    ax.plot(concurrency, total_p95, marker="s", label="Total p95", color="#ffd24a", linestyle="--")
    ax.set_xlabel("Concurrency")
    ax.set_ylabel("Latency (ms)")
    ax.set_title("Latency percentiles vs concurrency")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    ax = axes[2]
    ax.plot(concurrency, gpu_before, marker="o", label="before batch", color="#a0a4b0")
    ax.plot(concurrency, gpu_after, marker="o", label="after batch", color="#3ddc84")
    ax.set_xlabel("Concurrency")
    ax.set_ylabel("GPU SM utilization (%)")
    ax.set_ylim(0, 100)
    ax.set_title("GPU utilization before/after")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    fig.tight_layout()

    out_path = args.out or args.csv_path.with_suffix(".png")
    fig.savefig(out_path, dpi=140)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
