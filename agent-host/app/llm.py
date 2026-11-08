"""Model routing: pick a chat model per agent, with a fallback provider and usage tracking."""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, TypeVar

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from app.config import Settings
from app.observability import metrics as m
from app.observability.tracing import end_span, start_span

log = logging.getLogger("agent.llm")
T = TypeVar("T", bound=BaseModel)

# USD per 1M tokens (input, output). Unknown models cost 0 in the estimate.
PRICES = {"gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.5, 10.0), "claude-haiku": (1.0, 5.0),
          "claude-sonnet": (3.0, 15.0), "claude-opus": (15.0, 75.0)}


class BudgetExceeded(Exception):
    """Raised when a run has used more tokens than its budget."""


def _provider_default(settings: Settings, provider: str) -> str:
    return settings.openai_model if provider == "openai" else settings.anthropic_model


def model_name(settings: Settings, agent: str, provider: str | None = None) -> str:
    provider = provider or settings.model_provider
    override = getattr(settings, f"model_{agent}", None) or getattr(settings, f"model_{agent}_agent", None)
    # A Claude model name is meaningless to OpenAI and vice versa: fall back to the provider default.
    if override and ((provider == "openai") != override.lower().startswith("claude")):
        return override
    return _provider_default(settings, provider)


def build_model(settings: Settings, provider: str, name: str) -> BaseChatModel:
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=name, temperature=0, api_key=settings.openai_api_key, max_retries=2, timeout=60)
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(model=name, temperature=0, api_key=settings.anthropic_api_key, max_retries=2, timeout=60)


class UsageTracker(AsyncCallbackHandler):
    """Counts tokens and estimated cost per agent for one run, and enforces the token budget."""

    def __init__(self, budget: int):
        self.budget = budget
        self.by_agent: dict[str, dict[str, float]] = defaultdict(lambda: {"input": 0, "output": 0, "calls": 0, "cost_usd": 0.0})
        self._agent_of_run: dict[Any, tuple[str, str, float, Any]] = {}

    @property
    def total_tokens(self) -> int:
        return int(sum(v["input"] + v["output"] for v in self.by_agent.values()))

    @property
    def cost_usd(self) -> float:
        return round(sum(v["cost_usd"] for v in self.by_agent.values()), 6)

    def summary(self) -> dict[str, Any]:
        return {"total_tokens": self.total_tokens, "cost_usd": self.cost_usd, "budget": self.budget,
                "by_agent": {k: dict(v) for k, v in self.by_agent.items()}}

    async def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):
        meta = metadata or {}
        agent, model = meta.get("agent", "unknown"), meta.get("ls_model_name", "")
        self._agent_of_run[run_id] = (agent, model, time.perf_counter(), start_span("llm.call", agent=agent, model=model))

    async def on_llm_end(self, response, *, run_id, **kwargs):
        agent, model, started, span = self._agent_of_run.pop(run_id, ("unknown", "", time.perf_counter(), None))
        usage = {}
        try:
            usage = response.generations[0][0].message.usage_metadata or {}
        except (AttributeError, IndexError):
            pass
        i, o = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
        price = next((p for k, p in PRICES.items() if model.startswith(k)), (0, 0))
        row = self.by_agent[agent]
        row["input"] += i
        row["output"] += o
        row["calls"] += 1
        cost = (i * price[0] + o * price[1]) / 1_000_000
        row["cost_usd"] += cost
        m.LLM_CALLS.labels(agent, model or "unknown").inc()
        m.LLM_LATENCY.labels(agent, model or "unknown").observe(time.perf_counter() - started)
        m.LLM_TOKENS.labels(agent, model or "unknown", "input").inc(i)
        m.LLM_TOKENS.labels(agent, model or "unknown", "output").inc(o)
        m.LLM_COST.labels(agent, model or "unknown").inc(cost)
        end_span(span, input_tokens=i, output_tokens=o, cost_usd=round(cost, 6))
        if self.total_tokens > self.budget:
            m.BUDGET_EXCEEDED.inc()
            raise BudgetExceeded(f"token budget {self.budget} exceeded")


class ModelFactory:
    """Hands out chat models per agent. Structured calls fall back to the other provider
    when the primary keeps failing; transient errors are already retried by the client."""

    def __init__(self, settings: Settings):
        self.s = settings
        self.provider = settings.model_provider
        self.fallback_provider = "openai" if self.provider == "anthropic" else "anthropic"

    def _has_key(self, provider: str) -> bool:
        return bool(self.s.openai_api_key if provider == "openai" else self.s.anthropic_api_key)

    def get(self, agent: str) -> BaseChatModel:
        return build_model(self.s, self.provider, model_name(self.s, agent, self.provider))

    def fallback(self, agent: str) -> BaseChatModel | None:
        if not self._has_key(self.fallback_provider):
            return None
        return build_model(self.s, self.fallback_provider, model_name(self.s, agent, self.fallback_provider))

    async def structured(self, agent: str, schema: type[T], messages: list, config: dict | None = None) -> T:
        """Ask for output that must match `schema`. Tries the primary model, then the fallback."""
        cfg = {**(config or {}), "metadata": {**(config or {}).get("metadata", {}), "agent": agent}}
        candidates = [("primary", self.get(agent))]
        fb = self.fallback(agent)
        if fb is not None:
            candidates.append(("fallback", fb))
        last: Exception | None = None
        for label, model in candidates:
            try:
                return await model.with_structured_output(schema).ainvoke(messages, config=cfg)
            except BudgetExceeded:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("%s model failed for %s: %s", label, agent, e)
                m.RETRIES.labels("model_fallback" if label == "primary" else "model_failed").inc()
                last = e
        raise RuntimeError(f"all models failed for {agent}: {last}")
