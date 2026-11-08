"""Turns API payloads into plain rows for display. No Streamlit here, so everything is unit-testable."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

AGENT_LABELS = {
    "input_guard": "Input guardrails", "supervisor": "Supervisor", "order": "Order agent", "shipment": "Shipment agent",
    "policy": "Policy agent", "product": "Product agent", "risk": "Risk agent", "decision": "Decision agent",
    "approval_request": "Approval request", "approval_wait": "Approval wait", "execute": "Execution",
    "handle_no_approval": "No approval", "escalate": "Escalation", "response": "Response agent",
    "unsupported_intent": "Unsupported intent",
}
TERMINAL = {"COMPLETED", "PARTIALLY_COMPLETED", "REJECTED", "ESCALATED", "FAILED"}
ICONS = {"ok": "✅", "error": "❌", "COMPLETED": "✅", "PARTIALLY_COMPLETED": "⚠️", "REJECTED": "⛔", "ESCALATED": "🙋",
         "FAILED": "❌", "WAITING_FOR_APPROVAL": "⏳", "INVESTIGATING": "🔎", "STARTED": "🚀", "READY_FOR_EXECUTION": "▶️",
         "COMPENSATING": "↩️", "PENDING": "⏳", "APPROVED": "✅", "EXPIRED": "⌛"}


def label(node: str) -> str:
    return AGENT_LABELS.get(node, node.replace("_", " ").title())


def icon(status: str | None) -> str:
    return ICONS.get(status or "", "•")


def is_terminal(status: str | None) -> bool:
    return status in TERMINAL


def parse_ts(value: str | None) -> datetime | None:
    """ISO timestamp -> aware datetime. Services that omit the zone are in UTC."""
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_ms(ms: float | int | None) -> str:
    if ms is None:
        return "-"
    return f"{ms:.0f} ms" if ms < 1000 else f"{ms / 1000:.1f} s"


def fmt_usd(v: float | None) -> str:
    return "$0" if not v else f"${v:.4f}" if v < 1 else f"${v:.2f}"


# --------------------------------------------------------------- timeline / trace
def timeline_rows(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per step with start/end in ms since the first step began (parallel steps overlap)."""
    starts = [parse_ts(t.get("started_at")) for t in trace]
    t0 = min((s for s in starts if s), default=None)
    rows = []
    for t, s in zip(trace, starts):
        e = parse_ts(t.get("ended_at"))
        start_ms = int((s - t0).total_seconds() * 1000) if s and t0 else 0
        end_ms = int((e - t0).total_seconds() * 1000) if e and t0 else start_ms + int(t.get("latency_ms") or 0)
        rows.append({
            "step": label(t["agent"]), "node": t["agent"], "status": t.get("status", "ok"), "icon": icon(t.get("status", "ok")),
            "start_ms": start_ms, "end_ms": max(end_ms, start_ms + 1), "latency_ms": int(t.get("latency_ms") or 0),
            "retries": int(t.get("retries") or 0), "error": t.get("error") or "", "detail": t.get("detail") or "",
            "tool_calls": len(t.get("tool_calls") or []), "tokens": int(t.get("tokens") or 0)})
    return rows


def tool_call_rows(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every MCP tool call made by the agents, in order."""
    return [{"agent": label(t["agent"]), "tool": c["tool"], "ok": bool(c.get("ok")), "icon": icon("ok" if c.get("ok") else "error"),
             "error": (c.get("error") or "")[:160]} for t in trace for c in (t.get("tool_calls") or [])]


def usage_rows(usage: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Model calls, tokens and estimated cost per agent."""
    by = (usage or {}).get("by_agent", {})
    return [{"agent": ("RAG " + a[4:].replace("_", " ")) if a.startswith("rag_") else label(a), "calls": int(v.get("calls", 0)),
             "input_tokens": int(v.get("input", 0)), "output_tokens": int(v.get("output", 0)),
             "total_tokens": int(v.get("input", 0) + v.get("output", 0)), "cost_usd": float(v.get("cost_usd", 0))}
            for a, v in sorted(by.items(), key=lambda kv: -(kv[1].get("input", 0) + kv[1].get("output", 0)))]


def _ellipsis(text: str, limit: int = 290) -> str:
    """The agent host stores at most ~300 characters of reasoning; end on a word boundary with an ellipsis."""
    if len(text) < limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + " …"


def decision_summary(run: dict[str, Any], trace: list[dict[str, Any]]) -> dict[str, Any]:
    """What the supervisor and decision agent concluded, in one place."""
    by_node = {t["agent"]: t for t in trace}
    res = run.get("resolution") or {}
    return {"intent": run.get("intent"), "plan": run.get("plan") or [],
            "supervisor_reasoning": _ellipsis((by_node.get("supervisor") or {}).get("detail", "")),
            "action": res.get("action"), "refund_amount": res.get("refund_amount"), "replacement": res.get("replacement_product_id"),
            "risk": res.get("risk_level"), "reason": res.get("reason"), "evidence": res.get("evidence") or [],
            "approval_required": res.get("approval_required")}


# ------------------------------------------------------------------------- RAG
def candidate_rows(rag: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Every retrieved passage with its scores and what became of it."""
    if not rag:
        return []
    cited = {s["chunkId"]: s for s in rag.get("sources", [])}
    rows = []
    for c in (rag.get("diagnostics") or {}).get("candidates", []):
        text = (cited.get(c["chunkId"]) or {}).get("text") or ""
        rows.append({"policy": c["policyKey"], "version": c["version"], "section": c.get("section") or "",
                     "relevance": round(float(c.get("score") or 0), 3), "reranker": round(float(c.get("rerankScore") or 0), 3),
                     "in_evidence": c.get("inContext", False), "used_in_answer": c.get("cited", False),
                     "evidence": text})
    return rows


def grounding_summary(rag: dict[str, Any] | None) -> dict[str, Any]:
    if not rag:
        return {"label": "no evidence", "icon": "•", "issues": []}
    d = rag.get("diagnostics") or {}
    g = d.get("grounding") or {}
    if rag.get("insufficientEvidence"):
        status, ic = "insufficient evidence", "🚫"
    elif rag.get("grounded"):
        status, ic = "grounded", "✅"
    else:
        status, ic = "not grounded", "❌"
    return {"label": status, "icon": ic, "score": g.get("score"), "issues": g.get("issues") or [],
            "fallback": (d.get("generation") or {}).get("extractiveFallback"), "attempts": (d.get("generation") or {}).get("attempts"),
            "timings": d.get("timingsMs") or {}, "cache_hit": d.get("cacheHit"),
            "dropped": (d.get("context") or {}).get("dropped") or [], "reason": d.get("reason"), "latency_ms": d.get("latencyMs"),
            "degraded": (d.get("retrieval") or {}).get("degraded", False), "corpus_version": (d.get("retrieval") or {}).get("corpusVersion")}


# ----------------------------------------------------------------------- events
def event_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"time": (parse_ts(e.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)).strftime("%H:%M:%S"),
             "topic": e["topic"], "type": e.get("type") or "", "workflow": e.get("workflow_id") or "",
             "correlation_id": e.get("correlation_id") or "", "causation_id": e.get("causation_id") or "",
             "delivered": "✅" if e.get("sent_at") else "⏳", "event_id": e.get("event_id", ""),
             "payload": json.dumps(e.get("payload") or {}, indent=2, default=str)} for e in events]


def audit_rows(audit: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"time": (parse_ts(a.get("createdAt")) or datetime.min.replace(tzinfo=timezone.utc)).strftime("%H:%M:%S"),
             "event": a.get("eventType", ""), "agent": a.get("agentName") or "", "actor": a.get("actor") or "",
             "payload": a.get("payload") or ""} for a in audit]


# -------------------------------------------------------------------- approvals
def approval_rows(approvals: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    rows = []
    for a in approvals:
        exp = parse_ts(a.get("expiresAt"))
        left = int((exp - now).total_seconds()) if exp else None
        rows.append({**a, "seconds_left": left, "expires_in": ("expired" if left is not None and left <= 0
                                                                else f"{left // 60}m {left % 60:02d}s" if left is not None else "-")})
    return rows


# --------------------------------------------------------------------- workflows
def workflow_rows(workflows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"workflow": w["workflowId"], "status": f"{icon(w['status'])} {w['status']}", "order": w["orderId"], "customer": w["customerId"],
             "updated": (parse_ts(w.get("updatedAt")) or datetime.min.replace(tzinfo=timezone.utc)).strftime("%m-%d %H:%M:%S"),
             "message": ((w.get("state") or {}).get("customerMessage") or "")[:70]} for w in workflows]


def compensation_rows(workflows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Workflows that needed compensation or ended partially done, with the steps that ran."""
    rows = []
    for w in workflows:
        ex = (w.get("state") or {}).get("execution") or {}
        steps = ex.get("steps") or []
        if w["status"] in ("COMPENSATING", "PARTIALLY_COMPLETED") or ex.get("compensated"):
            for s in steps:
                rows.append({"workflow": w["workflowId"], "order": w["orderId"], "status": w["status"], "step": s.get("step"),
                             "ok": "✅" if s.get("ok") else "❌", "detail": s.get("detail", ""),
                             "compensation": "↩️" if s.get("step") == "release_inventory" else ""})
    return rows
