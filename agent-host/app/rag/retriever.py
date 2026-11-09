"""Retrieval client: one call to the policy search tool (hybrid search and rerank happen in policy-service), behind a circuit breaker."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from app.guardrails.tool_guardrails import TOOL_FAILURES
from app.rag.models import Chunk

log = logging.getLogger("rag.retriever")
SearchFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class RetrievalUnavailable(Exception):
    """Policy search is failing; the circuit breaker is open or the call failed."""


class CircuitBreaker:
    """Opens after `threshold` consecutive failures and refuses calls for `reset_after` seconds."""

    def __init__(self, threshold: int = 3, reset_after: float = 30.0, clock: Callable[[], float] = time.monotonic):
        self.threshold, self.reset_after, self.clock = threshold, reset_after, clock
        self.failures, self.opened_at = 0, None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        return self.clock() - self.opened_at >= self.reset_after      # half-open: let one attempt through

    def success(self) -> None:
        self.failures, self.opened_at = 0, None

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = self.clock()


class PolicyRetriever:
    def __init__(self, search: SearchFn, breaker: CircuitBreaker | None = None, timeout: float = 15.0, top_k: int = 6):
        self.search, self.breaker, self.timeout, self.k = search, breaker or CircuitBreaker(), timeout, top_k

    async def retrieve(self, query: str, filters: dict[str, Any]) -> dict[str, Any]:
        """Chunks best-first plus what the service said about confidence, degradation and its cache."""
        if not self.breaker.allow():
            raise RetrievalUnavailable("policy search circuit breaker is open")
        args = {"query": query, "top_k": self.k, **{k: v for k, v in filters.items() if v}}
        try:
            res = await asyncio.wait_for(self.search(args), timeout=self.timeout)
        except TOOL_FAILURES as e:
            self.breaker.failure()
            raise RetrievalUnavailable(f"policy search failed: {e}") from e
        self.breaker.success()
        d = res.get("diagnostics", {})
        return {"chunks": [Chunk.from_service(s) for s in res.get("sources", [])],
                "confidence": 0.0 if res.get("insufficientEvidence") else float(res.get("confidence") or 0),
                "corpus_version": d.get("corpusVersion") or "0", "degraded": bool(d.get("degraded")), "cache_hit": bool(d.get("cacheHit"))}
