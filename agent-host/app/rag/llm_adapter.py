"""Connects the pipeline's LlmPort to the project's ModelFactory (with provider fallback and usage tracking)."""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app.llm import ModelFactory
from app.rag import generator, query_rewriter, reranker
from app.rag.models import Chunk, ContextPack, GroundedAnswer


class ModelLlm:
    def __init__(self, models: ModelFactory, config: dict | None = None):
        self.models, self.config = models, config or {}

    async def rewrite(self, question: str) -> list[str]:
        r = await self.models.structured("rag_rewriter", query_rewriter.Rewrite, [
            SystemMessage(query_rewriter.SYSTEM), HumanMessage(f"<question>{question}</question>")], self.config)
        return r.queries

    async def rerank(self, question: str, chunks: list[Chunk], scope: str = "") -> reranker.Ranking:
        return await self.models.structured("rag_reranker", reranker.Ranking, [
            SystemMessage(reranker.SYSTEM), HumanMessage(reranker.passages_prompt(question, chunks, scope=scope))], self.config)

    async def generate(self, question: str, pack: ContextPack, feedback: str, scope: str = "") -> GroundedAnswer:
        return await self.models.structured("rag_generator", GroundedAnswer, [
            SystemMessage(generator.SYSTEM), HumanMessage(generator.user_prompt(question, pack, feedback, scope))], self.config)
