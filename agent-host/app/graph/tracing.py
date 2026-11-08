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
from app.observability import logctx, metrics as m
from app.observability.context import CURRENT_NODE
from app.observability.tracing import span

log = logging.getLogger("agent.graph")
_SKIP_INPUT = {"trace", "evidence", "usage", "request_id", "correlation_id", "errors"}


def _view(state) -> dict[str, Any]:
    """What an agent step 'sees': the non-empty fields of the case state, without bulky bookkeeping."""
    try:
        d = state.model_dump(mode="json")
    except AttributeError:
        d = dict(state)
    return {k: v for k, v in d.items() if k not in _SKIP_INPUT and v not in (None, "", [], {})}


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
            log_tokens = logctx.bind(agent=name, workflow_id=state.workflow_id, customer_id=state.customer_id,
                                     correlation_id=state.correlation_id or None)
            started = datetime.now(timezone.utc)
            t0 = time.perf_counter()
            log.info("AGENT > %s received input=%s", name, logctx.clean(_view(state)))
            entry: dict[str, Any] = {"agent": name, "started_at": started.isoformat(), "status": "ok", "retries": 0}
            try:
                with span(f"node.{name}", workflow_id=state.workflow_id):
                    update = await fn(state, config) or {}
            except GraphBubbleUp:
                log.info("AGENT | %s paused (waiting for a human decision)", name)
                logctx.unbind(log_tokens)
                raise
            except BudgetExceeded as e:
                update, entry["status"], entry["error"] = {"errors": [f"BUDGET_EXCEEDED: {e}"]}, "error", str(e)
            except Exception as e:  # noqa: BLE001
                log.exception("node %s failed", name)
                update, entry["status"], entry["error"] = {"errors": [f"NODE_ERROR_{name}: {str(e)[:160]}"]}, "error", str(e)[:300]
            out_view = {k: v for k, v in update.items() if k not in ("trace", "usage") and not k.startswith("_")}
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
            level = logging.WARNING if entry["status"] != "ok" else logging.INFO
            log.log(level, "AGENT < %s %s %dms tools=%s tokens=%s retries=%s detail=%s output=%s", name, entry["status"], entry["latency_ms"],
                    [f"{c['tool']}{'' if c['ok'] else '(FAILED)'}" for c in entry.get("tool_calls", [])], entry.get("tokens", 0), entry["retries"],
                    logctx.clean(entry.get("detail") or entry.get("error") or "", 300), logctx.clean(out_view))
            logctx.unbind(log_tokens)
            update["trace"] = [*update.get("trace", []), entry]
            return update
        return wrapper
    return deco
