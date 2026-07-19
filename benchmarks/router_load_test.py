"""
Load-test SLA fallback in app/router.py (primary → fast).

On this single-GPU WSL host, two vLLM engines crashed the VM (see
router/RESULTS.md), so both logical backends share one vLLM URL with
separate Orchestrator / SLA settings.

Usage:
    python router_load_test.py --requests 60 --concurrency 12
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from router import BackendConfig, ModelRouter  # noqa: E402

PROMPT = "Explain, in two sentences, why admission control matters for LLM serving."


async def fire_one(router: ModelRouter, i: int) -> dict:
    backend, degraded = router.resolve("primary")
    t0 = time.perf_counter()
    try:
        ticket_cm = backend.orchestrator.track()
        ticket = await ticket_cm.__aenter__()
    except Exception as e:
        return {"i": i, "backend": backend.config.name, "degraded": degraded, "error": f"queue-full: {e}"}

    try:
        stream = await backend.client.chat.completions.create(
            model=backend.config.model_name,
            messages=[{"role": "user", "content": PROMPT}],
            max_tokens=64,
            stream=True,
        )
        output_tokens = 0
        async for chunk in stream:
            if not chunk.choices:
                continue
            piece = getattr(chunk.choices[0].delta, "content", None) or ""
            if piece:
                if ticket.first_token_at is None:
                    ticket.first_token_at = time.perf_counter()
                output_tokens += 1
        ticket.output_tokens = output_tokens
        await ticket_cm.__aexit__(None, None, None)
        backend.metrics.record_completion(
            wait_ms=ticket.wait_ms, ttft_ms=ticket.ttft_ms,
            processing_ms=ticket.processing_ms, total_ms=ticket.total_ms,
            output_tokens=output_tokens,
        )
        return {
            "i": i, "backend": backend.config.name, "degraded": degraded,
            "ttft_ms": ticket.ttft_ms, "total_ms": (time.perf_counter() - t0) * 1000,
        }
    except Exception as e:
        await ticket_cm.__aexit__(type(e), e, e.__traceback__)
        backend.metrics.record_completion(
            wait_ms=ticket.wait_ms, ttft_ms=None, processing_ms=None,
            total_ms=ticket.total_ms, output_tokens=0, error=True,
        )
        return {"i": i, "backend": backend.config.name, "degraded": degraded, "error": str(e)}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--model-name", default="Qwen/Qwen2.5-VL-7B-Instruct")
    parser.add_argument("--requests", type=int, default=60)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--primary-max-concurrency", type=int, default=2)
    parser.add_argument("--primary-sla-ttft-ms", type=float, default=400.0)
    parser.add_argument("--fast-max-concurrency", type=int, default=8)
    args = parser.parse_args()

    router = ModelRouter(
        [
            BackendConfig(
                name="primary",
                base_url=args.base_url,
                model_name=args.model_name,
                sla_ttft_ms=args.primary_sla_ttft_ms,
                max_concurrency=args.primary_max_concurrency,
                max_queue_depth=32,
                fallback="fast",
            ),
            BackendConfig(
                name="fast",
                base_url=args.base_url,
                model_name=args.model_name,
                sla_ttft_ms=args.primary_sla_ttft_ms * 3,
                max_concurrency=args.fast_max_concurrency,
                max_queue_depth=32,
                fallback=None,
            ),
        ]
    )

    print(
        f"primary: max_concurrency={args.primary_max_concurrency} sla_ttft_ms={args.primary_sla_ttft_ms}\n"
        f"fast:    max_concurrency={args.fast_max_concurrency} sla_ttft_ms={args.primary_sla_ttft_ms * 3}\n"
        f"Firing {args.requests} requests at client-concurrency={args.concurrency}...\n"
    )

    sem = asyncio.Semaphore(args.concurrency)
    results: list[dict] = []

    async def worker(i: int) -> None:
        async with sem:
            results.append(await fire_one(router, i))
            snap = router.snapshot()
            p = snap["primary"]
            print(
                f"  req {i:>3} -> backend={results[-1]['backend']:<7} "
                f"degraded={results[-1]['degraded']!s:<5} "
                f"primary.ttft_p95={p['metrics']['latency_ms']['ttft']['p95']} "
                f"primary.breaching={p['is_breaching_sla']} "
                f"primary.queue={p['orchestrator']['queue_depth']}"
            )

    await asyncio.gather(*[worker(i) for i in range(args.requests)])

    degraded = [r for r in results if r["degraded"]]
    to_primary = [r for r in results if r["backend"] == "primary"]
    to_fast = [r for r in results if r["backend"] == "fast"]
    errors = [r for r in results if r.get("error")]

    print("\n=== Summary ===")
    print(f"Total requests:        {len(results)}")
    print(f"Routed to primary:     {len(to_primary)}")
    print(f"Routed to fast (degraded): {len(to_fast)}")
    print(f"Errors:                {len(errors)}")
    print(f"\nFinal router snapshot:")
    import json

    print(json.dumps(router.snapshot(), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
