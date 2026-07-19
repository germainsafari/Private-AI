"""
Async load generator for the inference gateway.

Hits the same /api/chat endpoint real users hit (not vLLM directly), so
results reflect the whole stack: FastAPI + the orchestrator's admission
control + vLLM's internal continuous batching. Sweeps concurrency levels,
records TTFT / total latency / throughput per level, and snapshots GPU
utilization before and after each level.

Usage:
    python bench_client.py --concurrency 1 4 8 16 --requests-per-level 12
    python bench_client.py --long-prompt --concurrency 1 4 --out results/long_prompt.csv
    python bench_client.py --vision --concurrency 1 4 --out results/vision.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

DEFAULT_PROMPT = (
    "Explain the difference between continuous batching and static batching "
    "in LLM inference serving, in about 150 words."
)

LONG_PROMPT = (
    "You are reviewing a technical design document for a distributed inference "
    "platform. " + ("The system must handle bursty traffic while maintaining low "
    "tail latency and high GPU utilization. " * 90) +
    "Summarize the three biggest architectural risks in about 150 words."
)

VISION_IMAGE_SOURCE_URL = "https://httpbin.org/image/jpeg"


def fetch_vision_data_url(source_url: str = VISION_IMAGE_SOURCE_URL) -> str:
    """
    Fetch a small test image once and embed it as a base64 data URL, so the
    benchmark loop itself has no external network dependency (and isn't at
    the mercy of a CDN's hotlink/thumbnail policy).
    """
    resp = httpx.get(source_url, timeout=10, follow_redirects=True)
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
    import base64

    encoded = base64.b64encode(resp.content).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


@dataclass
class RequestResult:
    ok: bool
    ttft_ms: Optional[float]
    total_ms: float
    output_chars: int
    status_code: Optional[int]
    error: Optional[str] = None


async def send_request(
    client: httpx.AsyncClient,
    base_url: str,
    prompt: str,
    max_tokens: int,
    image_url: Optional[str],
) -> RequestResult:
    content: object
    if image_url:
        content = [
            {"type": "image_url", "image_url": {"url": image_url}},
            {"type": "text", "text": prompt},
        ]
    else:
        content = prompt

    body = {
        "messages": [{"role": "user", "content": content}],
        "stream": True,
        "max_tokens": max_tokens,
    }

    t0 = time.perf_counter()
    ttft_ms: Optional[float] = None
    output_chars = 0
    try:
        async with client.stream("POST", f"{base_url}/api/chat", json=body, timeout=180) as resp:
            if resp.status_code == 429:
                return RequestResult(False, None, (time.perf_counter() - t0) * 1000, 0, 429, "queue full (429)")
            if resp.status_code != 200:
                text = await resp.aread()
                return RequestResult(
                    False, None, (time.perf_counter() - t0) * 1000, 0,
                    resp.status_code, text.decode(errors="ignore")[:200],
                )
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload:
                    continue
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if "delta" in obj:
                    if ttft_ms is None:
                        ttft_ms = (time.perf_counter() - t0) * 1000
                    output_chars += len(obj["delta"])
                elif obj.get("error"):
                    return RequestResult(
                        False, ttft_ms, (time.perf_counter() - t0) * 1000,
                        output_chars, 200, str(obj["error"]),
                    )
        return RequestResult(True, ttft_ms, (time.perf_counter() - t0) * 1000, output_chars, 200)
    except Exception as e:  # noqa: BLE001
        return RequestResult(False, ttft_ms, (time.perf_counter() - t0) * 1000, output_chars, None, str(e))


async def run_level(
    base_url: str,
    concurrency: int,
    num_requests: int,
    prompt: str,
    max_tokens: int,
    image_url: Optional[str],
) -> list[RequestResult]:
    sem = asyncio.Semaphore(concurrency)
    results: list[RequestResult] = []

    async with httpx.AsyncClient() as client:

        async def worker() -> None:
            async with sem:
                results.append(await send_request(client, base_url, prompt, max_tokens, image_url))

        await asyncio.gather(*[worker() for _ in range(num_requests)])
    return results


def percentile(values: list[float], p: float) -> Optional[float]:
    if not values:
        return None
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    k = (len(values) - 1) * (p / 100)
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    if lo == hi:
        return values[lo]
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def gpu_snapshot() -> dict:
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total,power.draw",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=5,
        )
        util, mem_used, mem_total, power = [x.strip() for x in out.strip().split(",")]
        return {
            "gpu_util_pct": float(util),
            "mem_used_mb": float(mem_used),
            "mem_total_mb": float(mem_total),
            "power_w": float(power),
        }
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def summarize(label: str, concurrency: int, wall_s: float, results: list[RequestResult]) -> dict:
    ok = [r for r in results if r.ok]
    ttfts = [r.ttft_ms for r in ok if r.ttft_ms is not None]
    totals = [r.total_ms for r in ok]
    total_chars = sum(r.output_chars for r in ok)
    # Rough chars->token heuristic (~4 chars/token) for a quick throughput read;
    # the gateway's own /api/metrics reports exact token counts from vLLM usage stats.
    approx_tokens = total_chars / 4.0

    return {
        "label": label,
        "concurrency": concurrency,
        "requests": len(results),
        "ok": len(ok),
        "errors": len(results) - len(ok),
        "wall_s": round(wall_s, 3),
        "ttft_p50_ms": round(percentile(ttfts, 50), 1) if ttfts else None,
        "ttft_p95_ms": round(percentile(ttfts, 95), 1) if ttfts else None,
        "ttft_p99_ms": round(percentile(ttfts, 99), 1) if ttfts else None,
        "total_p50_ms": round(percentile(totals, 50), 1) if totals else None,
        "total_p95_ms": round(percentile(totals, 95), 1) if totals else None,
        "total_p99_ms": round(percentile(totals, 99), 1) if totals else None,
        "approx_tokens_per_sec": round(approx_tokens / wall_s, 1) if wall_s > 0 else None,
    }


FIELDNAMES = [
    "label", "concurrency", "requests", "ok", "errors", "wall_s",
    "ttft_p50_ms", "ttft_p95_ms", "ttft_p99_ms",
    "total_p50_ms", "total_p95_ms", "total_p99_ms",
    "approx_tokens_per_sec",
    "gpu_util_before", "gpu_util_after", "mem_used_before_mb", "mem_used_after_mb",
]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the inference gateway")
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 4, 8, 16])
    parser.add_argument("--requests-per-level", type=int, default=12)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--long-prompt", action="store_true")
    parser.add_argument("--vision", action="store_true")
    parser.add_argument("--out", default="results/sweep.csv")
    parser.add_argument("--label-prefix", default="")
    args = parser.parse_args()

    prompt = LONG_PROMPT if args.long_prompt else args.prompt
    image_url = fetch_vision_data_url() if args.vision else None

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not out_path.exists() or out_path.stat().st_size == 0

    with out_path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()

        for c in args.concurrency:
            label = f"{args.label_prefix}c{c}"
            print(f"\n=== {label}: concurrency={c} requests={args.requests_per_level} ===")
            before = gpu_snapshot()
            t0 = time.perf_counter()
            results = await run_level(
                args.base_url, c, args.requests_per_level, prompt, args.max_tokens, image_url
            )
            wall_s = time.perf_counter() - t0
            after = gpu_snapshot()

            row = summarize(label, c, wall_s, results)
            row["gpu_util_before"] = before.get("gpu_util_pct")
            row["gpu_util_after"] = after.get("gpu_util_pct")
            row["mem_used_before_mb"] = before.get("mem_used_mb")
            row["mem_used_after_mb"] = after.get("mem_used_mb")

            writer.writerow(row)
            f.flush()
            print(json.dumps(row, indent=2))

            errored = [r for r in results if not r.ok]
            if errored:
                print(f"  ({len(errored)} errors, e.g. {errored[0].error})")


if __name__ == "__main__":
    asyncio.run(main())
