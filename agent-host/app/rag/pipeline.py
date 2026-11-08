"""The RAG pipeline: rewrite -> retrieve (parallel) -> rerank -> build context -> generate -> validate -> cache.

Every model stage has a safe fallback, and the order of preference is: a validated model answer, then a validated
extractive answer (sentences copied from the evidence), then an honest "could not verify". It never returns an
answer that failed grounding validation.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from app.llm import BudgetExceeded
from app.rag.answer_cache import AnswerCache, make_key
from app.rag.context_builder import apply_region_precedence, build_context
from app.rag.grounding_validator import content_stems, describe, validate
from app.rag.models import (INSUFFICIENT_ANSWER, Chunk, ContextPack, GroundedAnswer, GroundingReport, RagResult, RagSource,
                            Statement)
from app.rag.query_rewriter import clean_query, merge_queries
from app.rag.reranker import Ranking, apply_ranking
from app.rag.retriever import PolicyRetriever, RetrievalUnavailable

log = logging.getLogger("rag.pipeline")


class LlmPort(Protocol):
    async def rewrite(self, question: str) -> list[str]: ...
    async def rerank(self, question: str, chunks: list[Chunk], scope: str = "") -> Ranking: ...
    async def generate(self, question: str, pack: ContextPack, feedback: str, scope: str = "") -> GroundedAnswer: ...


@dataclass
class RagSettings:
    max_context_tokens: int = 700
    top_k: int = 4
    min_retrieval_confidence: float = 0.30
    use_rewrite: bool = True
    use_llm_rerank: bool = True
    min_support: float = 0.5
    llm_timeout: float = 25.0
    model_name: str = "default"


class RagPipeline:
    def __init__(self, retriever: PolicyRetriever, llm: LlmPort, cache: AnswerCache | None = None, settings: RagSettings | None = None):
        self.retriever, self.llm, self.cache, self.s = retriever, llm, cache or AnswerCache(None), settings or RagSettings()

    async def answer(self, question: str, filters: dict[str, Any] | None = None) -> RagResult:
        t0 = time.perf_counter()
        diag: dict[str, Any] = {"timingsMs": {}, "stages": []}
        filters = {k: v for k, v in (filters or {}).items() if v}
        question = clean_query(question, 500)
        if not question:
            return self._insufficient("empty question", diag, t0)

        scope = self._scope(filters)

        # 1. cheap first retrieval with the customer's own words (the policy service caches it)
        try:
            raw0 = await self._timed("retrieve", diag, self.retriever.retrieve_raw([question], filters))
        except RetrievalUnavailable as e:
            diag["retrievalError"] = str(e)
            return self._insufficient("policy search unavailable", diag, t0, unavailable=True)
        corpus = next((r.get("diagnostics", {}).get("corpusVersion") for r in raw0 if r.get("diagnostics", {}).get("corpusVersion")), "0")

        # 2. answer cache: a validated answer is a pure function of (question, filters, policy corpus, models), so a
        #    hit skips every model call. Any policy change bumps the corpus version and invalidates it.
        key = make_key(question, filters, corpus, [], self.s.model_name)
        cached = await self.cache.get(key)
        if cached is not None:
            cached.diagnostics = {**cached.diagnostics, "cacheHit": True, "latencyMs": self._ms(t0)}
            return cached

        # 3. rewrite (best effort) and search the extra query variants in parallel
        queries = [question]
        if self.s.use_rewrite:
            queries = await self._stage("rewrite", diag, lambda: self._rewrite(question), default=queries)
        diag["queries"] = queries
        raw = list(raw0)
        extra = [q for q in queries if q != question]
        if extra:
            try:
                raw += await self._timed("retrieve", diag, self.retriever.retrieve_raw(extra, filters))
            except RetrievalUnavailable as e:
                diag["stages"].append({"stage": "retrieve-extra", "error": str(e)[:150]})   # the first retrieval still stands
        ret = self.retriever.merge(raw)
        chunks: list[Chunk] = ret["chunks"]
        diag["retrieval"] = {"confidence": ret["confidence"], "candidates": len(chunks), "degraded": ret["degraded"],
                             "corpusVersion": corpus, "serviceCacheHits": ret["cache_hits"]}
        if not chunks or ret["confidence"] < self.s.min_retrieval_confidence:
            return self._insufficient("no sufficiently relevant policy found", diag, t0)
        chunks, overridden = apply_region_precedence(chunks, filters.get("region"))

        # 4. rerank with the model (listwise); keep retrieval order if it fails or returns nonsense
        relevance_confirmed: bool | None = None
        if self.s.use_llm_rerank and len(chunks) > 1:
            head = chunks[:8]
            ranking = await self._stage("rerank", diag, lambda: self.llm.rerank(question, head, scope), default=None)
            reordered = apply_ranking(head, ranking) if ranking else None
            if reordered is not None:
                relevance_confirmed = any(c.llm_relevant for c in reordered)
                diag["rerank"] = {"order": [c.chunk_id for c in reordered], "relevant": [c.chunk_id for c in reordered if c.llm_relevant]}
                if not relevance_confirmed:
                    return self._insufficient("the retrieved passages do not answer the question", diag, t0)
                chunks = [c for c in reordered if c.llm_relevant] + [c for c in reordered if not c.llm_relevant] + chunks[8:]
            else:
                diag["rerank"] = {"fallback": "retrieval order"}

        # 5. context within the token budget
        pack = build_context(question, chunks[: self.s.top_k + 2], self.s.max_context_tokens, self.s.top_k)
        pack.dropped += overridden
        diag["context"] = {"items": len(pack.items), "tokens": pack.tokens, "dropped": pack.dropped}
        if not pack.items:
            return self._insufficient("all retrieved passages were withheld or empty", diag, t0)

        # 6. generate + validate (one retry with feedback), then extractive fallback
        statements, report, attempts, fallback = await self._generate_validated(question, pack, diag, scope)
        diag["generation"] = {"attempts": attempts, "extractiveFallback": fallback}
        diag["grounding"] = report.model_dump() if report else None
        if statements is None or report is None or not report.passed:
            return self._insufficient("no answer passed grounding validation", diag, t0)
        if not statements:
            return self._insufficient("the model found no answer in the passages", diag, t0)

        result = self._compose(statements, report, pack, ret["confidence"], relevance_confirmed, diag, t0)
        if result.grounded:
            await self.cache.set(key, result)
        return result

    # ----------------------------------------------------------------- stages
    async def _rewrite(self, question: str) -> list[str]:
        return merge_queries(question, await self.llm.rewrite(question))

    async def _generate_validated(self, question: str, pack: ContextPack, diag: dict[str, Any], scope: str = ""):
        feedback, report = "", None
        for attempt in (1, 2):
            ans = await self._stage("generate", diag, lambda: self.llm.generate(question, pack, feedback, scope), default=None)
            if ans is None:
                break
            if ans.insufficient_evidence:
                return [], GroundingReport(passed=True, score=1.0), attempt, False
            report = validate(ans.statements, pack, self.s.min_support)
            if report.passed:
                return ans.statements, report, attempt, False
            feedback = describe(report)
            diag["stages"].append({"stage": "validate", "attempt": attempt, "rejected": feedback})
        # extractive fallback: copy the most relevant sentences from the evidence, then validate them too
        fallback = extractive_statements(question, pack)
        fb_report = validate(fallback, pack, self.s.min_support)
        if fb_report.passed:
            return fallback, fb_report, 2, True
        return None, report, 2, True

    def _compose(self, statements: list[Statement], report: GroundingReport, pack: ContextPack, retrieval_conf: float,
                 relevance_confirmed: bool | None, diag: dict[str, Any], t0: float) -> RagResult:
        used: list[int] = []
        for st in statements:
            for n in st.cites:
                if n not in used:
                    used.append(n)
        renumber = {n: i + 1 for i, n in enumerate(used)}      # [n] in the answer = position in sources[]
        answer = " ".join(f"{st.text.strip()} " + "".join(f"[{renumber[n]}]" for n in st.cites) for st in statements).strip()
        sources = []
        for n in used:
            c = pack.get(n).chunk
            sources.append(RagSource(policyId=c.policy_id, chunkId=c.chunk_id, policyKey=c.policy_key, version=c.version,
                                     source=c.title, section=c.section, score=c.score, rerankScore=c.rerank_score, text=pack.get(n).text))
        relevance = 1.0 if relevance_confirmed else 0.5 if relevance_confirmed is None else 0.0
        confidence = round(min(1.0, 0.4 * retrieval_conf + 0.3 * relevance + 0.3 * report.score), 3)
        diag["latencyMs"] = self._ms(t0)
        diag["cacheHit"] = False
        return RagResult(answer=answer, confidence=confidence, grounded=report.passed, policyVersion=sources[0].version,
                         sources=sources, insufficientEvidence=False, diagnostics=diag)

    def _insufficient(self, reason: str, diag: dict[str, Any], t0: float, unavailable: bool = False) -> RagResult:
        diag.update(reason=reason, latencyMs=self._ms(t0), cacheHit=False, unavailable=unavailable)
        return RagResult(answer=INSUFFICIENT_ANSWER, confidence=0.2, grounded=False, policyVersion=None, sources=[],
                         insufficientEvidence=True, diagnostics=diag)

    # ---------------------------------------------------------------- helpers
    async def _stage(self, name: str, diag: dict[str, Any], fn, default):
        """Run a model stage with a timeout. Any failure other than the token budget returns `default`."""
        try:
            return await self._timed(name, diag, asyncio.wait_for(fn(), timeout=self.s.llm_timeout))
        except BudgetExceeded:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("rag stage %s failed: %s", name, e)
            diag["stages"].append({"stage": name, "error": str(e)[:150]})
            return default

    async def _timed(self, name: str, diag: dict[str, Any], awaitable):
        t = time.perf_counter()
        try:
            return await awaitable
        finally:
            diag["timingsMs"][name] = diag["timingsMs"].get(name, 0) + self._ms(t)

    @staticmethod
    def _scope(filters: dict[str, Any]) -> str:
        """A sentence telling the model about the customer's scope, so regional rules are not mistaken for global ones."""
        parts = []
        if filters.get("region") and str(filters["region"]).upper() != "ALL":
            parts.append(f"The customer is in region {filters['region']}. A policy written for that region overrides the general policy on the same topic.")
        if filters.get("product_category"):
            parts.append(f"The item is in the category {filters['product_category']}.")
        return " ".join(parts)

    @staticmethod
    def _ms(t: float) -> int:
        return int((time.perf_counter() - t) * 1000)


def extractive_statements(question: str, pack: ContextPack, max_items: int = 2) -> list[Statement]:
    """Statements copied word for word from the top passages: the fallback when the model's answer is rejected."""
    q = content_stems(question)
    out: list[Statement] = []
    for item in pack.items[:max_items]:
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", item.text) if s.strip()]
        best = max(sentences, key=lambda s: len(q & content_stems(s)), default=None)
        if best:
            out.append(Statement(text=best.strip(), cites=[item.n]))
    return out
