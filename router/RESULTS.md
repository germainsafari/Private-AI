# Multi-Model SLA Routing — Results

`app/router.py` implements a `ModelRouter` that holds one independently
admission-controlled `Backend` per logical model (its own `Orchestrator` +
`MetricsRegistry`), and picks a backend per-request via `router.resolve(name)`.
If the requested backend's rolling **p95 time-to-first-token** exceeds its
configured `sla_ttft_ms`, the router transparently redirects the request to
that backend's configured `fallback` instead — spillover under load, not a
failure.

## A second real hardware constraint (and why this test uses one physical model)

Standing up a genuinely separate second model (`Qwen2.5-1.5B-Instruct`)
alongside the 7B-VL model on the same 32GB card surfaced a second real,
reproducible problem: **launching a second vLLM engine process while the
first is running crashed the whole WSL2 GPU-passthrough VM three separate
times**, across a wide range of `--gpu-memory-utilization` values (0.14
through 0.98) and with `--enforce-eager` to remove CUDA-graph overhead.
Investigation showed:

- vLLM's own free-memory pre-check, run from inside the WSL guest, reported
  ~30GB "free" even while the *other* vLLM process (visible to Windows'
  `nvidia-smi`) was already holding ~27GB — i.e. the two engines' memory
  accounting did not agree with each other, consistent with a WSL2/GPU-PV
  virtualization-level memory-visibility gap on this particular
  driver + RTX 5090 (Blackwell) + WSL2 combination.
- Whichever process actually tried to allocate against its (incorrectly
  optimistic) budget triggered a fault that took down the shared WSL VM,
  not just the Python process — `wsl --shutdown` + restart was required to
  recover each time.

This is a genuine finding, not a workaround for a bug in this repo's code —
`app/router.py` is written against the standard OpenAI-compatible client
interface and does not care whether "primary" and "fast" are two processes
on one GPU, two GPUs, or two machines. Given a stable multi-engine host, the
exact same code routes across genuinely distinct models.

To still exercise the **real SLA-breach → fallback decision path** under
real concurrent load without fighting this environment's virtualization bug
further, `benchmarks/router_load_test.py` configures two `Backend` instances
against the *same* running vLLM server, each with its own independent
`Orchestrator` (different `max_concurrency`) and its own `sla_ttft_ms`. The
routing logic being tested is identical either way.

## Setup

- `primary`: `max_concurrency=2`, `sla_ttft_ms=400ms`, `fallback="fast"`
- `fast`: `max_concurrency=8`, `sla_ttft_ms=1200ms`, no fallback
- Load: 40 requests, client-side concurrency 12, against the live
  Qwen2.5-VL-7B-Instruct backend.

Reproduce with:

```bash
python benchmarks/router_load_test.py --requests 40 --concurrency 10 \
  --primary-max-concurrency 2 --primary-sla-ttft-ms 400 --fast-max-concurrency 8
```

## Result

| | Requests routed | TTFT p50 | TTFT p95 | Breaching SLA at end? |
| --- | ---: | ---: | ---: | :---: |
| `primary` | 10 | 2395.5ms | 3866.8ms | **yes** |
| `fast` (fallback) | 30 | 80.8ms | 464.4ms | no |

The first 10 requests (bounded by client concurrency) were dispatched before
`primary` had accumulated enough TTFT samples to know it was in trouble —
this is expected: SLA detection is based on a rolling window of real
completions, not a prediction. From request 11 onward, every subsequent
request was transparently redirected to `fast` for the remainder of the run,
and `fast` never once breached its own (looser) SLA — it had real headroom
(`max_concurrency=8`) that `primary` (deliberately capped at 2 to force
saturation) didn't.

Zero errors across all 40 requests. `router.snapshot()` (exposed at
`GET /api/router/status`) shows exactly this at the end of the run:
`primary.degraded_count = 30`, `primary.is_breaching_sla = true`,
`fast.is_breaching_sla = false`.

## Takeaway

SLA-based routing turns "one backend is overloaded" into "some requests take
a slightly different path" instead of "requests queue indefinitely" or
"requests fail." Combined with the admission control from
[`RESULTS.md`](../RESULTS.md), there are now two independent, complementary
mechanisms for absorbing overload gracefully: **queue within a backend** (up
to its own concurrency ceiling) and **spill across backends** (once a
backend's SLA is breached) — the same two knobs a real inference-serving
control plane would use.
