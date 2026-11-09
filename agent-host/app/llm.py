"""Model routing: pick a chat model per agent, with a fallback provider and usage tracking."""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, TypeVar

import httpx
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.exceptions import LangChainException
from langchain_core.language_models import BaseChatModel
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel

from app.config import Settings
from app.observability import logctx, metrics as m
from app.observability.tracing import end_span, start_span

log = logging.getLogger("agent.llm")
T = TypeVar("T", bound=BaseModel)

# USD per 1M tokens (input, output). Unknown models cost 0 in the estimate.
PRICES = {"gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.5, 10.0), "claude-haiku": (1.0, 5.0),
          "claude-sonnet": (3.0, 15.0), "claude-opus": (15.0, 75.0)}


def _provider_errors() -> tuple[type[Exception], ...]:
    """Exceptions the model providers raise for bad responses, quota, network or auth problems (whichever are installed)."""
    found: list[type[Exception]] = []
    for module, name in (("openai", "OpenAIError"), ("anthropic", "AnthropicError"),
                         ("botocore.exceptions", "BotoCoreError"), ("botocore.exceptions", "ClientError")):
        try:
            found.append(getattr(__import__(module, fromlist=[name]), name))
        except (ImportError, AttributeError):
            continue
    return tuple(found)


# What a model call may expectedly fail with. Anything else (a bug, a spent token budget) is allowed to propagate.
MODEL_ERRORS: tuple[type[Exception], ...] = (
    RuntimeError, ValueError, TimeoutError, httpx.HTTPError, GraphRecursionError, LangChainException, *_provider_errors())


class BudgetExceeded(Exception):
    """Raised when a run has used more tokens than its budget."""


def _provider_default(settings: Settings, provider: str) -> str:
    if provider == "bedrock":
        return settings.bedrock_model_id
    return settings.openai_model if provider == "openai" else settings.anthropic_model


def model_name(settings: Settings, agent: str, provider: str | None = None) -> str:
    provider = provider or settings.model_provider
    override = getattr(settings, f"model_{agent}", None) or getattr(settings, f"model_{agent}_agent", None)
    # A model name only means something to its own provider: otherwise use the provider default.
    if override and _belongs_to(provider, override):
        return override
    return _provider_default(settings, provider)


def _belongs_to(provider: str, name: str) -> bool:
    n = name.lower()
    if provider == "bedrock":                       # Bedrock ids look like "anthropic.claude-...", "eu.anthropic...", or an inference-profile ARN
        return n.startswith("arn:") or "." in n.split(":")[0]
    if provider == "openai":
        return not n.startswith("claude") and "." not in n.split(":")[0]
    return n.startswith("claude")


# Classification, extraction, tool arguments, policy and risk decisions and structured output are deterministic.
# Only the customer-facing reply may vary a little, and it is still checked against the verified facts.
DETERMINISTIC_TEMPERATURE = 0.0
CUSTOMER_REPLY_TEMPERATURE = 0.3
CREATIVE_AGENTS = {"response"}


def temperature_for(agent: str) -> float:
    return CUSTOMER_REPLY_TEMPERATURE if agent in CREATIVE_AGENTS else DETERMINISTIC_TEMPERATURE


def build_model(settings: Settings, provider: str, name: str, temperature: float = DETERMINISTIC_TEMPERATURE) -> BaseChatModel:
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=name, temperature=temperature, api_key=settings.openai_api_key, max_retries=2, timeout=60)
    if provider == "bedrock":
        # Credentials come from the task/instance role; the guardrail (if configured) screens every call.
        from langchain_aws import ChatBedrockConverse
        kwargs: dict[str, Any] = {}
        if settings.bedrock_guardrail_id:
            kwargs["guardrail_config"] = {"guardrailIdentifier": settings.bedrock_guardrail_id,
                                          "guardrailVersion": settings.bedrock_guardrail_version, "trace": "enabled"}
        return ChatBedrockConverse(model=name, region_name=settings.aws_region, temperature=temperature, max_tokens=4096, **kwargs)
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(model=name, temperature=temperature, api_key=settings.anthropic_api_key, max_retries=2, timeout=60)


def _text_of(content: Any) -> str:
    if isinstance(content, list):
        return " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content or "")


def _describe_prompt(messages: list) -> str:
    """Prompt summary for the log: how many messages, and (if LOG_LLM_CONTENT) the system line and the latest input."""
    try:
        flat = [m_ for batch in messages for m_ in batch]
        size = sum(len(_text_of(getattr(m_, "content", ""))) for m_ in flat)
        head = f"messages={len(flat)} chars={size}"
        if not logctx.llm_content_enabled() or not flat:
            return head
        system = next((m_ for m_ in flat if getattr(m_, "type", "") == "system"), None)
        last = flat[-1]
        parts = [head]
        if system is not None:
            parts.append(f"system={logctx.clean(_text_of(getattr(system, 'content', '')), 200)}")
        parts.append(f"last[{getattr(last, 'type', '?')}]={logctx.clean(_text_of(getattr(last, 'content', '')))}")
        return " ".join(parts)
    except Exception:  # noqa: BLE001 - logging must not break a model call
        return "messages=<unavailable>"


def _describe_answer(response) -> str:
    """Never raises: logging must not be able to break a model call."""
    try:
        msg = response.generations[0][0].message
        content = getattr(msg, "content", "")
        calls = [f"{c.get('name')}({logctx.clean(c.get('args'), 300)})" for c in (getattr(msg, "tool_calls", None) or [])]
        if not logctx.llm_content_enabled():
            return f"chars={len(_text_of(content))} tool_calls={[c.split('(')[0] for c in calls]}"
        return f"tool_calls={calls} answer={logctx.clean(_text_of(content))}"
    except Exception:  # noqa: BLE001
        return "answer=<unavailable>"


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
        log.info("LLM > %s model=%s %s", agent, model, _describe_prompt(messages))
        self._agent_of_run[run_id] = (agent, model, time.perf_counter(), start_span("llm.call", agent=agent, model=model))

    async def on_llm_end(self, response, *, run_id, **kwargs):
        agent, model, started, span = self._agent_of_run.pop(run_id, ("unknown", "", time.perf_counter(), None))
        usage = {}
        try:
            usage = response.generations[0][0].message.usage_metadata or {}
        except (AttributeError, IndexError):
            pass
        i, o = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
        log.info("LLM < %s model=%s %.0fms tokens in=%s out=%s %s", agent, model, (time.perf_counter() - started) * 1000, i, o, _describe_answer(response))
        price = next((p for k, p in PRICES.items() if k in model), (0, 0))
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
        self.fallback_provider = "openai" if self.provider in ("anthropic", "bedrock") else "anthropic"

    def _has_key(self, provider: str) -> bool:
        if provider == "bedrock":
            return True                            # role-based credentials
        return bool(self.s.openai_api_key if provider == "openai" else self.s.anthropic_api_key)

    def get(self, agent: str) -> BaseChatModel:
        return build_model(self.s, self.provider, model_name(self.s, agent, self.provider), temperature_for(agent))

    def fallback(self, agent: str) -> BaseChatModel | None:
        if not self._has_key(self.fallback_provider):
            return None
        return build_model(self.s, self.fallback_provider, model_name(self.s, agent, self.fallback_provider), temperature_for(agent))

    async def structured(self, agent: str, schema: type[T], messages: list, config: dict | None = None) -> T:
        """Ask for output that must match `schema`. Tries the primary model, then the fallback."""
        cfg = {**(config or {}), "metadata": {**(config or {}).get("metadata", {}), "agent": agent}}
        candidates = [("primary", self.get(agent))]
        fb = self.fallback(agent)
        if fb is not None:
            candidates.append(("fallback", fb))
        last: BaseException | None = None
        for label, model in candidates:
            try:
                return await model.with_structured_output(schema).ainvoke(messages, config=cfg)
            except BudgetExceeded:
                raise
            except MODEL_ERRORS as e:
                log.warning("%s model failed for %s: %s", label, agent, e)
                m.RETRIES.labels("model_fallback" if label == "primary" else "model_failed").inc()
                last = e
        raise RuntimeError(f"all models failed for {agent}: {last}")
