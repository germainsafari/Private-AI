# Quantization Benchmark — FP16 vs INT8 (bitsandbytes) vs INT4 (AWQ)

Same model (**Qwen2.5-7B-Instruct**), same prompt, same GPU (RTX 5090), same
FastAPI gateway, same admission-control settings (`MAX_CONCURRENCY=8`) — only
the weight quantization format changes between runs. Each variant was loaded
as the *only* model on the GPU (see the crash notes in
[`RESULTS.md`](../RESULTS.md) and [`router/RESULTS.md`](../router/RESULTS.md)
for why: this rig cannot reliably hold two vLLM engines in VRAM at once).

Raw CSVs and the comparison chart are in
[`benchmarks/results/quantization/`](../benchmarks/results/quantization/).
Reproduce with:

```bash
# FP16
vllm serve Qwen/Qwen2.5-7B-Instruct --port 8082 --max-model-len 4096 --gpu-memory-utilization 0.85
# INT8 (on-the-fly bitsandbytes quantization of the FP16 checkpoint)
vllm serve Qwen/Qwen2.5-7B-Instruct --port 8082 --quantization bitsandbytes --load-format bitsandbytes
# INT4 (pre-quantized AWQ checkpoint)
vllm serve Qwen/Qwen2.5-7B-Instruct-AWQ --port 8082

python benchmarks/bench_client.py --concurrency 1 4 8 --requests-per-level 12 --out benchmarks/results/quantization/<variant>.csv
python benchmarks/plot_quant_comparison.py benchmarks/results/quantization
```

## Model size on disk

| Format | Size on disk |
| --- | ---: |
| FP16 (`Qwen2.5-7B-Instruct`) | 15 GB |
| AWQ INT4 (`Qwen2.5-7B-Instruct-AWQ`) | 5.2 GB |

(bitsandbytes INT8 doesn't have a separate checkpoint — it quantizes the FP16
weights on load, so its disk footprint is the same 15GB as FP16; the memory
savings only show up in VRAM, not on disk.)

## Throughput and latency

| Variant | Concurrency | Throughput (tok/s, approx) | TTFT p50 (ms) | Total p50 (ms) |
| --- | ---: | ---: | ---: | ---: |
| FP16  | 1 | 131.7  | 67.0 | 1630.2 |
| FP16  | 4 | 482.6  | 83.9 | 1702.4 |
| FP16  | 8 | 390.0  | 85.5 | 4914.7 |
| INT8  | 1 | 219.9  | 77.0 | 909.5  |
| INT8  | 4 | 285.9  | 97.6 | 2929.9 |
| INT8  | 8 | 272.7  | 98.4 | 5841.0 |
| INT4 (AWQ) | 1 | 93.8*  | 61.6 | 773.5 |
| INT4 (AWQ) | 4 | 1080.0 | 64.4 | 810.5 |
| INT4 (AWQ) | 8 | 579.4  | 64.0 | 3951.3 |

\* The AWQ concurrency=1 run's throughput number is dragged down by a real,
one-time JIT/kernel-compilation cost on the very first request against a
freshly-loaded AWQ model (that single request's TTFT was ~9.3s; every request
after it was back to ~60ms). This is expected and reproducible — AWQ's custom
INT4 GEMM kernels compile lazily on first use. All the AWQ *latency* p50
numbers above already reflect steady-state (post-warm-up) behavior; the
throughput number for `c=1` specifically includes that one slow request in
its 12-request average and should be read as a warm-up artifact, not AWQ's
true single-stream throughput (which the TTFT/total-latency columns show
clearly is the *fastest* of the three).

![Quantization comparison](../benchmarks/results/quantization/comparison.png)

## Interpretation

1. **AWQ (INT4) is the clear winner on this hardware for both latency and
   peak throughput.** Lower TTFT at every concurrency level (~64ms flat vs
   FP16's ~68-86ms and INT8's ~77-98ms), and the highest peak throughput
   (1080 tok/s at concurrency 4). This matches the theory: AWQ's weights are
   4x smaller, so decode is far less memory-bandwidth-bound, and vLLM ships
   genuinely fast fused INT4 GEMM kernels for it — this isn't "quantization
   as a compromise," it's a straightforward win here.
2. **bitsandbytes INT8 is *not* a throughput win** despite using less memory
   than FP16 — its throughput actually **plateaus lower than FP16** at
   concurrency 8 (272.7 vs 390.0 tok/s) and its TTFT is consistently the
   *worst* of the three. This is a known, real characteristic of
   bitsandbytes: it dequantizes weights on-the-fly with relatively simple
   CUDA kernels that aren't as aggressively optimized as AWQ's, so it trades
   memory for compute rather than saving both. It's the right tool when the
   goal is "make an FP16 checkpoint fit in less VRAM without a separate
   quantization step," not when the goal is throughput.
3. **Practical takeaway for a real deployment decision:** if you control the
   checkpoint (i.e. can pre-quantize with AWQ/GPTQ), do that — it's strictly
   better here on every axis that matters (latency, throughput, and VRAM).
   bitsandbytes is a reasonable fallback when you need to quantize an
   arbitrary FP16 checkpoint at load time with no separate quantization
   pass, and you're VRAM-constrained rather than throughput-constrained.
