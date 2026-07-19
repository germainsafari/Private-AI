"""
Multi-model, SLA-aware router.

Each backend (a distinct model server, e.g. a large multimodal model and a
small fast text model) gets its own admission-controlled Orchestrator and its
own MetricsRegistry, so load on one backend never starves another. The router
picks a backend by logical name and applies SLA-based graceful degradation:
if a backend's recent time-to-first-token is breaching its configured target,
new requests are transparently redirected to a configured fallback backend
(e.g. spill from the big model onto the small one) rather than queuing
indefinitely or failing outright.

This is a software analogue of what a cluster-level inference scheduler
(routing across heterogeneous accelerators/models under SLA constraints) does
at a larger scale.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from openai import AsyncOpenAI

from metrics import MetricsRegistry
from orchestrator import Orchestrator


@dataclass
class BackendConfig:
    name: str
    base_url: str
    model_name: str
    api_key: str = "not-needed"
    sla_ttft_ms: float = 1000.0
    max_concurrency: int = 8
    max_queue_depth: int = 32
    fallback: Optional[str] = None
    request_timeout_s: float = 120.0


class Backend:
    def __init__(self, config: BackendConfig, gpu_device_index: int = 0):
        self.config = config
        self.client = AsyncOpenAI(
            base_url=config.base_url, api_key=config.api_key, timeout=config.request_timeout_s
        )
        self.orchestrator = Orchestrator(
            max_concurrency=config.max_concurrency, max_queue_depth=config.max_queue_depth
        )
        self.metrics = MetricsRegistry(gpu_device_index=gpu_device_index)
        self.degraded_count = 0

    def is_breaching_sla(self) -> bool:
        p95 = self.metrics.ttft_ms.percentile(95)
        return p95 is not None and p95 > self.config.sla_ttft_ms


class ModelRouter:
    def __init__(self, configs: list[BackendConfig], gpu_device_index: int = 0):
        self.backends: dict[str, Backend] = {
            cfg.name: Backend(cfg, gpu_device_index=gpu_device_index) for cfg in configs
        }
        self.default_name = configs[0].name if configs else None

    def resolve(self, requested_name: Optional[str]) -> tuple[Backend, bool]:
        """
        Resolve a logical model name to a Backend. Returns (backend, degraded)
        where degraded=True means the request was redirected away from the
        originally-requested backend because it is breaching its SLA.
        """
        name = requested_name or self.default_name
        backend = self.backends.get(name) if name else None
        if backend is None:
            backend = next(iter(self.backends.values()))

        if backend.is_breaching_sla() and backend.config.fallback:
            fallback = self.backends.get(backend.config.fallback)
            if fallback is not None and fallback is not backend:
                backend.degraded_count += 1
                return fallback, True

        return backend, False

    def snapshot(self) -> dict:
        return {
            name: {
                "model": b.config.model_name,
                "sla_ttft_ms": b.config.sla_ttft_ms,
                "fallback": b.config.fallback,
                "is_breaching_sla": b.is_breaching_sla(),
                "degraded_count": b.degraded_count,
                "orchestrator": {
                    "queue_depth": b.orchestrator.queue_depth,
                    "active_requests": b.orchestrator.active_requests,
                    "max_concurrency": b.orchestrator.max_concurrency,
                    "max_queue_depth": b.orchestrator.max_queue_depth,
                },
                "metrics": b.metrics.snapshot(orchestrator=b.orchestrator),
            }
            for name, b in self.backends.items()
        }
