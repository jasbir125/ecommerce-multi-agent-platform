"""Wires the RAG pipeline to the real model factory, the policy search tool (over MCP) and Redis."""
from __future__ import annotations

from typing import Any

from app.graph.deps import Deps
from app.mcp.client import McpGateway
from app.rag.answer_cache import AnswerCache
from app.rag.llm_adapter import ModelLlm
from app.rag.models import RagResult
from app.rag.pipeline import RagPipeline, RagSettings
from app.rag.retriever import PolicyRetriever


def build_pipeline(deps: Deps, gateway: McpGateway, config: dict | None = None) -> RagPipeline:
    s = deps.settings

    async def search(args: dict[str, Any]) -> dict[str, Any]:
        return await gateway.call("search_policy", args)

    from app.llm import model_name
    return RagPipeline(
        PolicyRetriever(search, deps.search_breaker),
        ModelLlm(deps.models, config),
        AnswerCache(deps.redis, s.rag_answer_cache_ttl),
        RagSettings(max_context_tokens=s.rag_max_context_tokens, top_k=s.rag_top_k,
                    min_retrieval_confidence=s.rag_min_retrieval_confidence, use_rewrite=s.rag_use_rewrite,
                    use_llm_rerank=s.rag_use_llm_rerank, min_support=s.rag_min_support, llm_timeout=s.rag_llm_timeout,
                    model_name=model_name(s, "rag_generator")))


def result_for_llm(result: RagResult) -> str:
    """What a ReAct agent sees from ask_policy: the answer, its citations and the confidence."""
    return result.model_dump_json()
