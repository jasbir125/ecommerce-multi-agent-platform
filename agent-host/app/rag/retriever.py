"""Retrieval client: runs the query variants in parallel through the policy search tool and merges the results."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from app.rag.models import Chunk

log = logging.getLogger("rag.retriever")
SearchFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class RetrievalUnavailable(Exception):
    """Policy search is failing; the circuit breaker is open or every query failed."""


class CircuitBreaker:
    """Opens after `threshold` consecutive failures and refuses calls for `reset_after` seconds."""

    def __init__(self, threshold: int = 3, reset_after: float = 30.0, clock: Callable[[], float] = time.monotonic):
        self.threshold, self.reset_after, self.clock = threshold, reset_after, clock
        self.failures, self.opened_at = 0, None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if self.clock() - self.opened_at >= self.reset_after:     # half-open: let one attempt through
            return True
        return False

    def success(self) -> None:
        self.failures, self.opened_at = 0, None

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = self.clock()


class Retrieval(dict):
    """Merged result: chunks ordered best-first, plus what the service said about confidence."""


class PolicyRetriever:
    def __init__(self, search: SearchFn, breaker: CircuitBreaker | None = None, timeout: float = 15.0, per_query_k: int = 6):
        self.search, self.breaker, self.timeout, self.k = search, breaker or CircuitBreaker(), timeout, per_query_k

    async def retrieve(self, queries: list[str], filters: dict[str, Any]) -> Retrieval:
        return self.merge(await self.retrieve_raw(queries, filters))

    async def retrieve_raw(self, queries: list[str], filters: dict[str, Any]) -> list[dict[str, Any]]:
        """Run the searches in parallel and return the raw service responses of those that succeeded."""
        if not self.breaker.allow():
            raise RetrievalUnavailable("policy search circuit breaker is open")
        results = await asyncio.gather(*(self._one(q, filters) for q in queries), return_exceptions=True)
        ok = [r for r in results if isinstance(r, dict)]
        if not ok:
            self.breaker.failure()
            raise RetrievalUnavailable(f"all {len(queries)} search calls failed: {results[0]}")
        self.breaker.success()
        return ok

    async def _one(self, query: str, filters: dict[str, Any]) -> dict[str, Any]:
        args = {"query": query, "top_k": self.k, **{k: v for k, v in filters.items() if v}}
        return await asyncio.wait_for(self.search(args), timeout=self.timeout)

    @staticmethod
    def merge(results: list[dict[str, Any]]) -> Retrieval:
        """Reciprocal-rank fusion across query variants: a chunk found by several variants ranks higher."""
        by_id: dict[str, Chunk] = {}
        for res in results:
            for rank, s in enumerate(res.get("sources", [])):
                c = Chunk.from_service(s)
                cur = by_id.get(c.chunk_id)
                if cur is None:
                    by_id[c.chunk_id] = cur = c
                elif c.rerank_score > cur.rerank_score:
                    c.rrf = cur.rrf
                    by_id[c.chunk_id] = cur = c
                cur.rrf += 1.0 / (60 + rank + 1)
        chunks = sorted(by_id.values(), key=lambda c: (c.rrf, c.rerank_score), reverse=True)
        diags = [r.get("diagnostics", {}) for r in results]
        return Retrieval(chunks=chunks,
                         confidence=max((float(r.get("confidence") or 0) for r in results if not r.get("insufficientEvidence")), default=0.0),
                         corpus_version=next((d.get("corpusVersion") for d in diags if d.get("corpusVersion")), "0"),
                         degraded=any(d.get("degraded") for d in diags), cache_hits=sum(1 for d in diags if d.get("cacheHit")),
                         queries=len(results))
