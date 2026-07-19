"""Plot prefill_decode.csv: prefill time and per-token decode time vs prompt length."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    with args.csv_path.open(newline="") as f:
        rows = list(csv.DictReader(f))

    x = [int(r["prompt_tokens_target"]) for r in rows]
    prefill = [float(r["prefill_ms"]) for r in rows]
    decode = [float(r["decode_ms_per_token"]) for r in rows]

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    fig.suptitle("Prefill vs. decode cost scaling (Qwen2.5-VL-7B)")

    axes[0].plot(x, prefill, marker="o", color="#ff8a3d")
    axes[0].set_xlabel("Prompt length (tokens, approx)")
    axes[0].set_ylabel("Prefill time (ms)")
    axes[0].set_title("Prefill: scales with input length")
    axes[0].grid(alpha=0.3)

    axes[1].plot(x, decode, marker="o", color="#5a9bff")
    axes[1].set_xlabel("Prompt length (tokens, approx)")
    axes[1].set_ylabel("Per-token decode time (ms)")
    axes[1].set_title("Decode: flat regardless of input length")
    axes[1].set_ylim(0, max(decode) * 1.4)
    axes[1].grid(alpha=0.3)

    fig.tight_layout()
    out_path = args.out or args.csv_path.with_suffix(".png")
    fig.savefig(out_path, dpi=140)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
