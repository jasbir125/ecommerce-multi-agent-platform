"""The agent-host side of RAG: retrieve (policy-service does hybrid search + rerank) -> build context -> generate -> validate -> cite.

The order of preference is: a validated model answer, then a validated extractive answer (sentences copied from the
evidence), then an honest "could not verify". It never returns an answer that failed grounding validation.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from app.guardrails.input_guardrails import mask_pii
from app.llm import MODEL_ERRORS, BudgetExceeded
from app.observability import logctx, metrics as m
from app.observability.tracing import span
from app.rag.context_builder import apply_region_precedence, build_context
from app.rag.grounding_validator import content_stems, describe, validate
from app.rag.models import (INSUFFICIENT_ANSWER, INSTRUCTION_LIKE, Chunk, ContextPack, GroundedAnswer, GroundingReport, RagResult, RagSource,
                            Statement)
from app.rag.retriever import PolicyRetriever, RetrievalUnavailable

log = logging.getLogger("rag.pipeline")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def clean_query(q: str, limit: int = 500) -> str:
    return _CTRL.sub(" ", mask_pii(q)).strip()[:limit]


class Generator(Protocol):
    async def generate(self, question: str, pack: ContextPack, feedback: str, scope: str = "") -> GroundedAnswer: ...


@dataclass
class RagSettings:
    max_context_tokens: int = 700
    top_k: int = 4
    min_retrieval_confidence: float = 0.30
    min_support: float = 0.5
    llm_timeout: float = 25.0


class RagPipeline:
    def __init__(self, retriever: PolicyRetriever, llm: Generator, settings: RagSettings | None = None):
        self.retriever, self.llm, self.s = retriever, llm, settings or RagSettings()

    async def answer(self, question: str, filters: dict[str, Any] | None = None) -> RagResult:
        log.info("RAG > question=%s filters=%s", logctx.clean(question, 300), logctx.clean(filters or {}, 200))
        res = await self._answer(question, filters)
        d = res.diagnostics or {}
        log.info("RAG < grounded=%s insufficient=%s confidence=%s cache_hit=%s sources=%s dropped=%s timings_ms=%s answer=%s",
                 res.grounded, res.insufficientEvidence, res.confidence, d.get("cacheHit"),
                 [f"{x.policyKey}@v{x.version}({x.rerankScore})" for x in res.sources], (d.get("context") or {}).get("dropped"),
                 d.get("timingsMs"), logctx.clean(res.answer, 500))
        return res

    async def _answer(self, question: str, filters: dict[str, Any] | None = None) -> RagResult:
        t0 = time.perf_counter()
        diag: dict[str, Any] = {"timingsMs": {}, "stages": []}
        filters = {k: v for k, v in (filters or {}).items() if v}
        question = clean_query(question)
        if not question:
            return self._insufficient("empty question", diag, t0)
        scope = self._scope(filters)

        # 1. retrieve: the policy service runs the hybrid search, applies the filters and reranks (and caches in Redis)
        try:
            ret = await self._timed("retrieve", diag, self.retriever.retrieve(question, filters))
        except RetrievalUnavailable as e:
            diag["retrievalError"] = str(e)
            return self._insufficient("policy search unavailable", diag, t0, unavailable=True)
        chunks: list[Chunk] = ret["chunks"]
        m.RAG_RELEVANCE.observe(ret["confidence"])
        if ret["degraded"]:
            m.RAG_DEGRADED.inc()
        diag["retrieval"] = {"confidence": ret["confidence"], "candidates": len(chunks), "degraded": ret["degraded"],
                             "corpusVersion": ret["corpus_version"], "serviceCacheHits": int(ret["cache_hit"])}
        diag["cacheHit"] = ret["cache_hit"]
        if not chunks or ret["confidence"] < self.s.min_retrieval_confidence:
            return self._insufficient("no sufficiently relevant policy found", diag, t0)
        chunks, overridden = apply_region_precedence(chunks, filters.get("region"))

        # 2. context within the token budget; suspected prompt injection is withheld and reported
        pack = build_context(question, chunks[: self.s.top_k + 2], self.s.max_context_tokens, self.s.top_k)
        pack.dropped += overridden
        seen_ids = {d.split(":", 1)[1] for d in pack.dropped if ":" in d}
        for c in chunks:                                  # report injection even when the chunk was ranked out of the evidence window
            if c.chunk_id not in seen_ids and (c.injection_suspected or INSTRUCTION_LIKE.search(c.text or "")):
                pack.dropped.append(f"{'withheld' if c.injection_suspected else 'injection'}:{c.chunk_id}")
        diag["context"] = {"items": len(pack.items), "tokens": pack.tokens, "dropped": pack.dropped}
        diag["candidates"] = candidates_report(chunks, pack)
        if not pack.items:
            return self._insufficient("all retrieved passages were withheld or empty", diag, t0)

        # 3. generate + validate (one retry with feedback), then extractive fallback
        statements, report, attempts, fallback = await self._generate_validated(question, pack, diag, scope)
        diag["generation"] = {"attempts": attempts, "extractiveFallback": fallback}
        diag["grounding"] = report.model_dump() if report else None
        if statements is None or report is None or not report.passed:
            return self._insufficient("no answer passed grounding validation", diag, t0)
        if not statements:
            return self._insufficient("the model found no answer in the passages", diag, t0)
        return self._compose(statements, report, pack, ret["confidence"], diag, t0)

    # ----------------------------------------------------------------- stages
    async def _generate_validated(self, question: str, pack: ContextPack, diag: dict[str, Any], scope: str = ""):
        feedback, report = "", None
        for attempt in (1, 2):
            ans = await self._stage("generate", diag, lambda: self.llm.generate(question, pack, feedback, scope), default=None)
            if ans is None:
                break
            if ans.insufficient_evidence:
                return [], GroundingReport(passed=True, score=1.0), attempt, False
            report = validate(ans.statements, pack, self.s.min_support)
            m.RAG_VALIDATION.labels("passed" if report.passed else "rejected").inc()
            for issue in report.issues:
                m.RAG_VALIDATION_ISSUES.labels(issue.code).inc()
                m.guardrail("rag", issue.code)
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
                 diag: dict[str, Any], t0: float) -> RagResult:
        used: list[int] = []
        for st in statements:
            for n in st.cites:
                if n not in used:
                    used.append(n)
        renumber = {n: i + 1 for i, n in enumerate(used)}      # [n] in the answer = position in sources[]
        cited_ids = {pack.get(n).chunk.chunk_id for n in used}
        for row in diag["candidates"]:
            row["cited"] = row["chunkId"] in cited_ids
        answer = " ".join(f"{st.text.strip()} " + "".join(f"[{renumber[n]}]" for n in st.cites) for st in statements).strip()
        sources = []
        for n in used:
            c = pack.get(n).chunk
            sources.append(RagSource(policyId=c.policy_id, chunkId=c.chunk_id, policyKey=c.policy_key, version=c.version,
                                     source=c.title, section=c.section, score=c.score, rerankScore=c.rerank_score, text=pack.get(n).text))
        confidence = round(min(1.0, 0.5 * retrieval_conf + 0.5 * report.score), 3)
        diag["latencyMs"] = self._ms(t0)
        m.RAG_ANSWERS.labels("extractive_fallback" if diag.get("generation", {}).get("extractiveFallback") else "grounded").inc()
        m.RAG_CONFIDENCE.observe(confidence)
        m.RAG_RERANK_TOP.observe(max((s.rerankScore for s in sources), default=0.0))
        return RagResult(answer=answer, confidence=confidence, grounded=report.passed, policyVersion=sources[0].version,
                         sources=sources, insufficientEvidence=False, diagnostics=diag)

    def _insufficient(self, reason: str, diag: dict[str, Any], t0: float, unavailable: bool = False) -> RagResult:
        diag.update(reason=reason, latencyMs=self._ms(t0), unavailable=unavailable)
        diag.setdefault("cacheHit", False)
        m.RAG_ANSWERS.labels("unavailable" if unavailable else "insufficient").inc()
        return RagResult(answer=INSUFFICIENT_ANSWER, confidence=0.2, grounded=False, policyVersion=None, sources=[],
                         insufficientEvidence=True, diagnostics=diag)

    # ---------------------------------------------------------------- helpers
    async def _stage(self, name: str, diag: dict[str, Any], fn, default):
        """Run a model stage with a timeout. Any failure other than the token budget returns `default`."""
        try:
            return await self._timed(name, diag, asyncio.wait_for(fn(), timeout=self.s.llm_timeout))
        except BudgetExceeded:
            raise
        except MODEL_ERRORS as e:
            log.warning("rag stage %s failed: %s", name, e)
            diag["stages"].append({"stage": name, "error": str(e)[:150]})
            return default

    async def _timed(self, name: str, diag: dict[str, Any], awaitable):
        t = time.perf_counter()
        try:
            with span(f"rag.{name}"):
                return await awaitable
        finally:
            diag["timingsMs"][name] = diag["timingsMs"].get(name, 0) + self._ms(t)
            m.RAG_STAGE.labels(name).observe(time.perf_counter() - t)

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


def candidates_report(chunks: list[Chunk], pack: ContextPack) -> list[dict[str, Any]]:
    """Everything that was retrieved and what became of it: in the evidence, cited by the answer."""
    in_ctx = {i.chunk.chunk_id for i in pack.items}
    return [{"chunkId": c.chunk_id, "policyKey": c.policy_key, "version": c.version, "section": c.section,
             "score": c.score, "rerankScore": c.rerank_score, "inContext": c.chunk_id in in_ctx, "cited": False} for c in chunks[:12]]


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


def build_pipeline(deps, gateway, config: dict | None = None) -> RagPipeline:
    """Wires the pipeline to the real model factory and the policy search tool (over MCP)."""
    from app.rag.generator import ModelGenerator
    s = deps.settings

    async def search(args: dict[str, Any]) -> dict[str, Any]:
        return await gateway.call("search_policy", args)

    return RagPipeline(PolicyRetriever(search, deps.search_breaker), ModelGenerator(deps.models, config),
                       RagSettings(max_context_tokens=s.rag_max_context_tokens, top_k=s.rag_top_k,
                                   min_retrieval_confidence=s.rag_min_retrieval_confidence, min_support=s.rag_min_support,
                                   llm_timeout=s.rag_llm_timeout))


def result_for_llm(result: RagResult) -> str:
    """What a ReAct agent sees from ask_policy: the answer, its citations and the confidence."""
    return result.model_dump_json()
