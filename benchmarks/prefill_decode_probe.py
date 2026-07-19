"""
Empirically isolate prefill time from per-token decode time on the live
gateway, as motivation for prefill/decode disaggregation (see
disaggregation/RESULTS.md for why this repo documents the *motivation*
empirically rather than running genuinely separate prefill/decode workers).

Method: for each prompt length, fire one request with max_tokens=1 (the
response's TTFT is ~pure prefill time: one forward pass over the whole
prompt, then stop) and one request with max_tokens=N (TTFT is still ~prefill
time, and (total_time - ttft) / (N-1) is the marginal per-token decode time).

Usage:
    python prefill_decode_probe.py --out results/prefill_decode.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import time
from pathlib import Path

import httpx

# Built from repeated filler sentences so token count scales predictably.
FILLER = "The quick brown fox jumps over the lazy dog near the river bank. "


def make_prompt(approx_tokens: int) -> str:
    # ~1.3 tokens per word for this filler text; repeat until long enough.
    words_needed = int(approx_tokens / 1.3)
    text = (FILLER * ((words_needed // len(FILLER.split())) + 2))
    words = text.split()[:words_needed]
    return " ".join(words) + "\n\nRespond with a short acknowledgement."


async def fire(client: httpx.AsyncClient, base_url: str, prompt: str, max_tokens: int) -> dict:
    body = {"messages": [{"role": "user", "content": prompt}], "stream": True, "max_tokens": max_tokens}
    t0 = time.perf_counter()
    ttft_ms = None
    output_tokens = 0
    async with client.stream("POST", f"{base_url}/api/chat", json=body, timeout=120) as resp:
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            obj = json.loads(payload)
            if "delta" in obj:
                if ttft_ms is None:
                    ttft_ms = (time.perf_counter() - t0) * 1000
                output_tokens += 1
    total_ms = (time.perf_counter() - t0) * 1000
    return {"ttft_ms": ttft_ms, "total_ms": total_ms, "output_tokens": output_tokens}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--prompt-tokens", type=int, nargs="+", default=[50, 300, 800, 1800, 3000])
    parser.add_argument("--decode-tokens", type=int, default=150)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", default="results/prefill_decode.csv")
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    async with httpx.AsyncClient() as client:
        for n_tokens in args.prompt_tokens:
            prompt = make_prompt(n_tokens)
            prefill_samples = []
            decode_samples = []
            for _ in range(args.repeats):
                r1 = await fire(client, args.base_url, prompt, max_tokens=1)
                if r1["ttft_ms"] is not None:
                    prefill_samples.append(r1["ttft_ms"])

                r2 = await fire(client, args.base_url, prompt, max_tokens=args.decode_tokens)
                if r2["ttft_ms"] is not None and r2["output_tokens"] > 1:
                    per_token = (r2["total_ms"] - r2["ttft_ms"]) / (r2["output_tokens"] - 1)
                    decode_samples.append(per_token)

            prefill_ms = sum(prefill_samples) / len(prefill_samples) if prefill_samples else None
            decode_ms_per_token = sum(decode_samples) / len(decode_samples) if decode_samples else None
            row = {
                "prompt_tokens_target": n_tokens,
                "prefill_ms": round(prefill_ms, 2) if prefill_ms else None,
                "decode_ms_per_token": round(decode_ms_per_token, 3) if decode_ms_per_token else None,
            }
            rows.append(row)
            print(json.dumps(row))

    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["prompt_tokens_target", "prefill_ms", "decode_ms_per_token"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
