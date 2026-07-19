"""
Admission control in front of the inference backend.

vLLM continuous-batches sequences inside the engine. This module limits how
many requests may be in flight, bounds the waiting queue (HTTP 429 when full),
and records per-request queue wait vs processing time vs TTFT.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional


class QueueFullError(Exception):
    """Raised when the admission queue is already at capacity."""


@dataclass
class RequestTicket:
    request_id: str
    queued_at: float
    admitted_at: Optional[float] = None
    first_token_at: Optional[float] = None
    completed_at: Optional[float] = None
    output_tokens: int = 0
    error: Optional[str] = None

    @property
    def wait_ms(self) -> Optional[float]:
        """Time spent waiting for admission (queueing delay)."""
        if self.admitted_at is None:
            return None
        return (self.admitted_at - self.queued_at) * 1000

    @property
    def ttft_ms(self) -> Optional[float]:
        """End-to-end time to first token, from arrival, including queue wait."""
        if self.first_token_at is None:
            return None
        return (self.first_token_at - self.queued_at) * 1000

    @property
    def processing_ms(self) -> Optional[float]:
        """Time actually spent generating, once admitted (excludes queue wait)."""
        if self.admitted_at is None or self.completed_at is None:
            return None
        return (self.completed_at - self.admitted_at) * 1000

    @property
    def total_ms(self) -> Optional[float]:
        """Full end-to-end latency, from arrival to completion."""
        if self.completed_at is None:
            return None
        return (self.completed_at - self.queued_at) * 1000


class Orchestrator:
    """Bounded-concurrency admission controller with queue-depth backpressure."""

    def __init__(self, max_concurrency: int = 8, max_queue_depth: int = 64):
        self.max_concurrency = max_concurrency
        self.max_queue_depth = max_queue_depth
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._queue_depth = 0
        self._active = 0
        self._lock = asyncio.Lock()
        self.recent_tickets: dict[str, RequestTicket] = {}

    @property
    def queue_depth(self) -> int:
        return self._queue_depth

    @property
    def active_requests(self) -> int:
        return self._active

    @asynccontextmanager
    async def track(self) -> AsyncIterator[RequestTicket]:
        """
        Admit a request, yield a ticket to record timing against, and release
        the concurrency slot on exit (success or failure). Raises
        QueueFullError immediately if the admission queue is saturated,
        without ever acquiring a concurrency slot.
        """
        ticket = RequestTicket(request_id=str(uuid.uuid4()), queued_at=time.perf_counter())

        async with self._lock:
            if self._queue_depth >= self.max_queue_depth:
                raise QueueFullError(
                    f"admission queue at capacity ({self._queue_depth}/{self.max_queue_depth})"
                )
            self._queue_depth += 1

        try:
            await self._semaphore.acquire()
        except asyncio.CancelledError:
            async with self._lock:
                self._queue_depth -= 1
            raise

        async with self._lock:
            self._queue_depth -= 1
            self._active += 1
        ticket.admitted_at = time.perf_counter()

        try:
            yield ticket
        except Exception as e:
            ticket.error = str(e)
            raise
        finally:
            ticket.completed_at = time.perf_counter()
            self._semaphore.release()
            async with self._lock:
                self._active -= 1
            self.recent_tickets[ticket.request_id] = ticket
            if len(self.recent_tickets) > 500:
                oldest = next(iter(self.recent_tickets))
                self.recent_tickets.pop(oldest, None)
