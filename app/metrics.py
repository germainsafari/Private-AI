"""
GPU telemetry (pynvml/NVML) and rolling request stats: wait, TTFT, processing
latency percentiles, and tokens/sec.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional

try:
    import pynvml

    _PYNVML_IMPORTED = True
except ImportError:  # pragma: no cover - environment without NVML bindings
    _PYNVML_IMPORTED = False


class GPUSampler:
    """Thin wrapper around NVML with graceful degradation when unavailable
    (e.g. running the gateway on a non-GPU host such as Render)."""

    def __init__(self, device_index: int = 0):
        self.device_index = device_index
        self._handle = None
        self.available = False
        self._init_error: Optional[str] = None

        if not _PYNVML_IMPORTED:
            self._init_error = "pynvml not installed"
            return

        try:
            pynvml.nvmlInit()
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(device_index)
            self.available = True
        except Exception as e:  # noqa: BLE001 - want to swallow any NVML error
            self._init_error = str(e)

    def sample(self) -> dict:
        if not self.available:
            return {"available": False, "error": self._init_error}
        try:
            util = pynvml.nvmlDeviceGetUtilizationRates(self._handle)
            mem = pynvml.nvmlDeviceGetMemoryInfo(self._handle)
            name = pynvml.nvmlDeviceGetName(self._handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="ignore")

            power_w: Optional[float] = None
            try:
                power_w = pynvml.nvmlDeviceGetPowerUsage(self._handle) / 1000.0
            except Exception:
                pass

            temp_c: Optional[int] = None
            try:
                temp_c = pynvml.nvmlDeviceGetTemperature(
                    self._handle, pynvml.NVML_TEMPERATURE_GPU
                )
            except Exception:
                pass

            return {
                "available": True,
                "name": name,
                "gpu_util_pct": util.gpu,
                "mem_util_pct": util.memory,
                "mem_used_mb": round(mem.used / (1024**2), 1),
                "mem_total_mb": round(mem.total / (1024**2), 1),
                "mem_free_mb": round(mem.free / (1024**2), 1),
                "power_w": round(power_w, 1) if power_w is not None else None,
                "temp_c": temp_c,
            }
        except Exception as e:  # noqa: BLE001
            return {"available": False, "error": str(e)}


class RollingWindow:
    """Fixed-capacity rolling window of scalar samples with percentile lookup."""

    def __init__(self, maxlen: int = 1000):
        self._values: deque[float] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def add(self, value: float) -> None:
        with self._lock:
            self._values.append(value)

    def snapshot(self) -> list[float]:
        with self._lock:
            return list(self._values)

    def percentile(self, p: float) -> Optional[float]:
        values = sorted(self.snapshot())
        if not values:
            return None
        if len(values) == 1:
            return values[0]
        k = (len(values) - 1) * (p / 100)
        lo = int(k)
        hi = min(lo + 1, len(values) - 1)
        if lo == hi:
            return round(values[lo], 2)
        frac = k - lo
        return round(values[lo] + (values[hi] - values[lo]) * frac, 2)

    def mean(self) -> Optional[float]:
        values = self.snapshot()
        if not values:
            return None
        return round(sum(values) / len(values), 2)


class MetricsRegistry:
    """Process-wide registry of latency/throughput/error counters."""

    def __init__(self, gpu_device_index: int = 0):
        self.gpu = GPUSampler(gpu_device_index)

        self.wait_ms = RollingWindow()
        self.ttft_ms = RollingWindow()
        self.processing_ms = RollingWindow()
        self.total_ms = RollingWindow()

        self._token_events: deque[tuple[float, int]] = deque(maxlen=4000)
        self._lock = threading.Lock()

        self.total_requests = 0
        self.total_errors = 0
        self.total_rejected = 0
        self.total_tokens = 0
        self._started_at = time.time()

    def record_completion(
        self,
        *,
        wait_ms: Optional[float],
        ttft_ms: Optional[float],
        processing_ms: Optional[float],
        total_ms: Optional[float],
        output_tokens: int,
        error: bool = False,
    ) -> None:
        if wait_ms is not None:
            self.wait_ms.add(wait_ms)
        if ttft_ms is not None:
            self.ttft_ms.add(ttft_ms)
        if processing_ms is not None:
            self.processing_ms.add(processing_ms)
        if total_ms is not None:
            self.total_ms.add(total_ms)

        now = time.time()
        with self._lock:
            self.total_requests += 1
            if error:
                self.total_errors += 1
            if output_tokens:
                self.total_tokens += output_tokens
                self._token_events.append((now, output_tokens))

    def record_rejection(self) -> None:
        with self._lock:
            self.total_rejected += 1

    def tokens_per_second(self, window_s: float = 10.0) -> float:
        now = time.time()
        with self._lock:
            recent = [(t, n) for t, n in self._token_events if now - t <= window_s]
        if not recent:
            return 0.0
        total_tokens = sum(n for _, n in recent)
        span = max(now - min(t for t, _ in recent), 1.0)
        return round(total_tokens / span, 2)

    def snapshot(self, orchestrator=None) -> dict:
        with self._lock:
            totals = {
                "total_requests": self.total_requests,
                "total_errors": self.total_errors,
                "total_rejected": self.total_rejected,
                "total_tokens": self.total_tokens,
            }

        data = {
            "timestamp": time.time(),
            "uptime_s": round(time.time() - self._started_at, 1),
            "gpu": self.gpu.sample(),
            "requests": totals,
            "throughput": {
                "tokens_per_second": self.tokens_per_second(),
            },
            "latency_ms": {
                "wait": {
                    "p50": self.wait_ms.percentile(50),
                    "p95": self.wait_ms.percentile(95),
                    "p99": self.wait_ms.percentile(99),
                },
                "ttft": {
                    "p50": self.ttft_ms.percentile(50),
                    "p95": self.ttft_ms.percentile(95),
                    "p99": self.ttft_ms.percentile(99),
                },
                "processing": {
                    "p50": self.processing_ms.percentile(50),
                    "p95": self.processing_ms.percentile(95),
                    "p99": self.processing_ms.percentile(99),
                },
                "total": {
                    "p50": self.total_ms.percentile(50),
                    "p95": self.total_ms.percentile(95),
                    "p99": self.total_ms.percentile(99),
                },
            },
        }

        if orchestrator is not None:
            data["orchestrator"] = {
                "queue_depth": orchestrator.queue_depth,
                "active_requests": orchestrator.active_requests,
                "max_concurrency": orchestrator.max_concurrency,
                "max_queue_depth": orchestrator.max_queue_depth,
            }

        return data
