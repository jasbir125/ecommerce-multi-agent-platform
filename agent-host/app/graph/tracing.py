"""Decorator that records a trace entry for every graph node (timeline, retries, errors, tool calls)."""
from __future__ import annotations

import functools
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from langgraph.errors import GraphBubbleUp

from app.graph.deps import deps_of
from app.llm import BudgetExceeded
from app.observability import metrics as m
from app.observability.context import CURRENT_NODE
from app.observability.tracing import span

log = logging.getLogger("agent.graph")


def traced(name: str):
    """Wrap a node `async fn(state, config) -> dict`.

    - adds a trace entry (start/end/status/retries/tool calls),
    - strips private "_" keys the node uses to pass details to the tracer,
    - turns an unexpected exception into an error entry so the graph can still reach a safe end state
      (a LangGraph interrupt must pass through untouched)."""
    def deco(fn: Callable[..., Awaitable[dict]]):
        @functools.wraps(fn)
        async def wrapper(state, config) -> dict:
            deps = deps_of(config)
            ctx = deps.runs.get(state.workflow_id)
            calls_before = len(ctx.tool_log) if ctx else 0
            gateway_before = len(getattr(ctx.gateway, "call_log", [])) if ctx else 0
            node_token = CURRENT_NODE.set(name)
            started = datetime.now(timezone.utc)
            t0 = time.perf_counter()
            entry: dict[str, Any] = {"agent": name, "started_at": started.isoformat(), "status": "ok", "retries": 0}
            try:
                with span(f"node.{name}", workflow_id=state.workflow_id):
                    update = await fn(state, config) or {}
            except GraphBubbleUp:
                raise
            except BudgetExceeded as e:
                update, entry["status"], entry["error"] = {"errors": [f"BUDGET_EXCEEDED: {e}"]}, "error", str(e)
            except Exception as e:  # noqa: BLE001
                log.exception("node %s failed", name)
                update, entry["status"], entry["error"] = {"errors": [f"NODE_ERROR_{name}: {str(e)[:160]}"]}, "error", str(e)[:300]
            entry["retries"] = update.pop("_retries", 0)
            entry["detail"] = update.pop("_detail", "")
            update.pop("_tool_errors", None)
            CURRENT_NODE.reset(node_token)
            m.NODE_RESULTS.labels(name, entry["status"]).inc()
            m.NODE_DURATION.labels(name).observe(time.perf_counter() - t0)
            if entry["retries"]:
                m.RETRIES.labels("decision").inc(entry["retries"])
            entry["ended_at"] = datetime.now(timezone.utc).isoformat()
            entry["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            ctx = deps.runs.get(state.workflow_id)
            if ctx:
                mine = [e for e in ctx.tool_log[calls_before:] if e["agent"] == name]
                entry["tool_calls"] = [{"tool": e["tool"], "ok": e.get("ok", False), "error": e.get("error")} for e in mine]
                entry["tool_calls"] += [{"tool": c["tool"], "ok": c["ok"], "error": c["error"]}
                                        for c in getattr(ctx.gateway, "call_log", [])[gateway_before:] if c.get("node") == name]
                entry["tokens"] = int(ctx.usage.by_agent.get(name, {}).get("input", 0) + ctx.usage.by_agent.get(name, {}).get("output", 0))
            update["trace"] = [*update.get("trace", []), entry]
            return update
        return wrapper
    return deco
