# Multi-model SLA routing — results

`app/router.py` keeps one `Backend` per logical model (own `Orchestrator` + `MetricsRegistry`). If rolling **p95 TTFT** exceeds `sla_ttft_ms`, new requests go to the configured `fallback`.

## Single-GPU constraint

Starting a second vLLM process (`Qwen2.5-1.5B-Instruct`) next to the 7B-VL model crashed the WSL2 GPU VM three times, across `--gpu-memory-utilization` from 0.14–0.98 and with `--enforce-eager`.

- Inside WSL, vLLM's free-memory check reported ~30GB free while Windows `nvidia-smi` showed the other process holding ~27GB — inconsistent memory visibility under WSL2 GPU-PV on this driver + RTX 5090 setup.
- The allocating process faulted the shared WSL VM; recovery needed `wsl --shutdown`.

`app/router.py` only talks OpenAI-compatible HTTP; it does not care whether backends are one GPU, two GPUs, or two hosts. On a host that can run two engines, the same code targets distinct model URLs.

For this load test, `benchmarks/router_load_test.py` uses two `Backend` instances against **one** vLLM server, with different `max_concurrency` and `sla_ttft_ms`, to exercise the SLA → fallback path without a second engine.

## Setup

- `primary`: `max_concurrency=2`, `sla_ttft_ms=400ms`, `fallback="fast"`
- `fast`: `max_concurrency=8`, `sla_ttft_ms=1200ms`, no fallback
- 40 requests, client concurrency 12, model Qwen2.5-VL-7B-Instruct

```bash
python benchmarks/router_load_test.py --requests 40 --concurrency 10 \
  --primary-max-concurrency 2 --primary-sla-ttft-ms 400 --fast-max-concurrency 8
```

## Result

| | Requests routed | TTFT p50 | TTFT p95 | Breaching SLA at end? |
| --- | ---: | ---: | ---: | :---: |
| `primary` | 10 | 2395.5ms | 3866.8ms | **yes** |
| `fast` (fallback) | 30 | 80.8ms | 464.4ms | no |

The first ~10 requests ran before `primary` had enough TTFT samples to detect breach. From request 11 onward, traffic went to `fast`. Zero errors. End state via `GET /api/router/status`: `primary.degraded_count = 30`, `primary.is_breaching_sla = true`, `fast.is_breaching_sla = false`.

## Notes

Two levers under load: queue inside a backend (up to its concurrency cap), then spill to a fallback when its SLA is breached. See also [`RESULTS.md`](../RESULTS.md).
