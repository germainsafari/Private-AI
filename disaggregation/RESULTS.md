# Prefill / decode timing

## Background

- **Prefill** — forward pass over the full prompt; compute-bound; cost grows with input length.
- **Decode** — one new token per step against resident KV cache; memory-bandwidth-bound; per-token cost is roughly flat vs prompt length.

Running both in one continuous batch means a long prefill can delay decode steps for other in-flight requests. Serving stacks that split prefill and decode (vLLM disaggregated prefill, DistServe, Mooncake, etc.) put those phases on separate workers with KV transfer between them.

## Measurements (single GPU)

`benchmarks/prefill_decode_probe.py` hits the live gateway: `max_tokens=1` ≈ prefill (TTFT); `max_tokens=150` then gives per-token decode cost after the first token.

| Prompt length (tokens) | Prefill time (ms) | Decode time / token (ms) |
| ---: | ---: | ---: |
| 50\*   | 225.9 | 13.5 |
| 300  | 24.1  | 15.3 |
| 800  | 30.3  | 13.9 |
| 1800 | 41.5  | 15.3 |
| 3000 | 44.9  | 15.0 |

\* First row includes cold warm-up after model load (same pattern as AWQ first request in [`quantization/RESULTS.md`](../quantization/RESULTS.md)). Rows from 300 tokens are steady-state.

![Prefill vs decode scaling](../benchmarks/results/prefill_decode.png)

From 300 → 3000 tokens, prefill roughly doubles; decode stays ~14–15ms/token.

## Full disaggregation on this machine

This vLLM build (`0.19.2rc1.dev`) exposes `EngineArgs.kv_transfer_config` (connectors include `NixlConnector`, `P2pNcclConnector`, `MooncakeConnector`, `LMCacheConnectorV1`, etc.). Example shape:

```bash
vllm serve Qwen/Qwen2.5-7B-Instruct --port 8090 \
  --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_producer"}'

vllm serve Qwen/Qwen2.5-7B-Instruct --port 8091 \
  --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_consumer"}'
```

This rig has **one GPU**. Prefill/decode split is meant for separate accelerators. Two concurrent vLLM engines already crashed WSL2 GPU-PV here (see [`RESULTS.md`](../RESULTS.md), [`router/RESULTS.md`](../router/RESULTS.md)), so a live two-process disagg run was not pursued. This doc records the config surface and the measured prefill/decode cost split instead.
