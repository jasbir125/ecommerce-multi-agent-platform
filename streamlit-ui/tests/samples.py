"""Realistic payloads (shapes copied from the live services) and a fake API for UI tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from api_client import ApiError

T0 = datetime(2026, 11, 8, 10, 0, 0, tzinfo=timezone.utc)


def ts(ms: int) -> str:
    return (T0 + timedelta(milliseconds=ms)).isoformat()


def step(agent, start, took, status="ok", tools=(), tokens=0, detail="", retries=0, error=None):
    e = {"agent": agent, "started_at": ts(start), "ended_at": ts(start + took), "latency_ms": took, "status": status, "retries": retries,
         "detail": detail, "tool_calls": [{"tool": t, "ok": True, "error": None} for t in tools], "tokens": tokens}
    if error:
        e["error"] = error
    return e


TRACE = [step("input_guard", 0, 1), step("supervisor", 5, 1800, tokens=300, detail="delayed order recovery"),
         step("order", 1810, 2400, tools=["get_order_status", "get_payment_status"], tokens=500),
         step("shipment", 1812, 2900, tools=["get_shipment_status"], tokens=420),
         step("policy", 4720, 7800, tools=["ask_policy"], tokens=1300), step("product", 4721, 2300, tools=["search_products"], tokens=600),
         step("risk", 4722, 1), step("decision", 12600, 1900, tokens=800, detail="Lost shipment; policy allows a refund"),
         step("approval_request", 14500, 150), step("approval_wait", 14660, 20, status="error", error="boom", retries=1),
         step("execute", 14700, 900), step("response", 15600, 1400, tokens=250)]

RAG = {
    "answer": "Orders delayed by three (3) or more days are eligible for a full refund. [1]", "confidence": 0.91, "grounded": True,
    "policyVersion": "2", "insufficientEvidence": False,
    "sources": [{"policyId": "9", "chunkId": "1", "policyKey": "refund-policy", "version": "2", "source": "Refund Policy",
                 "section": "Delayed shipments", "score": 0.97, "rerankScore": 0.81,
                 "text": "Orders delayed by three (3) or more days are eligible for a full refund."}],
    "diagnostics": {"queries": ["my parcel is late", "refund eligibility delayed delivery"], "cacheHit": False, "latencyMs": 4100,
                    "timingsMs": {"retrieve": 120, "rewrite": 900, "generate": 1500},
                    "retrieval": {"confidence": 0.7, "candidates": 3, "degraded": False, "corpusVersion": "5"},
                    "context": {"items": 2, "tokens": 120, "dropped": ["injection:77"]},
                    "generation": {"attempts": 1, "extractiveFallback": False},
                    "grounding": {"passed": True, "score": 1.0, "issues": []},
                    "candidates": [{"chunkId": "1", "policyKey": "refund-policy", "version": "2", "section": "Delayed shipments", "score": 0.97,
                                    "rerankScore": 0.81, "llmRelevant": True, "inContext": True, "cited": True},
                                   {"chunkId": "2", "policyKey": "refund-policy", "version": "2", "section": "Refund method and timing",
                                    "score": 0.95, "rerankScore": 0.5, "llmRelevant": False, "inContext": True, "cited": False}]}}

RUN = {"workflowId": "WF-TEST000001", "customerId": "CUST-1001", "orderId": "ORD-7842", "status": "WAITING_FOR_APPROVAL",
       "intent": "DELAYED_ORDER_RECOVERY", "plan": ["order", "shipment", "policy"], "waitingForApproval": True, "approvalId": "APR-TEST000001",
       "approvalStatus": "PENDING",
       "order": {"order_id": "ORD-7842", "status": "SHIPPED", "payment_status": "CAPTURED", "total_amount": 129.99, "currency": "USD"},
       "shipment": {"order_id": "ORD-7842", "verified": True, "status": "LOST", "delay_days": 5, "exception_type": "LOST"},
       "policy": {"refund_eligible": True, "policy_key": "refund-policy", "policy_version": "2", "rag_confidence": 0.91, "grounded": True,
                  "citations": [{"policy_key": "refund-policy", "version": "2", "source": "Refund Policy"}], "rag": RAG},
       "product": {"product_id": "PRD-101", "price": 99.99}, "risk": {"level": "MEDIUM", "score": 0.4, "factors": ["HIGH_VALUE_ORDER"]},
       "resolution": {"action": "REFUND_AND_REPLACE", "refund_amount": 129.99, "replacement_product_id": "PRD-101", "approval_required": True,
                      "risk_level": "MEDIUM", "reason": "Lost shipment; policy allows a refund", "evidence": ["shipment LOST"]},
       "execution": None, "customerResponse": None, "errors": [],
       "usage": {"total_tokens": 4170, "cost_usd": 0.00084, "budget": 60000,
                 "by_agent": {"decision": {"input": 700, "output": 100, "calls": 1, "cost_usd": 0.0002},
                              "rag_generator": {"input": 900, "output": 120, "calls": 1, "cost_usd": 0.0002}}}}

WORKFLOW = {"workflowId": "WF-TEST000001", "customerId": "CUST-1001", "orderId": "ORD-7842", "status": "WAITING_FOR_APPROVAL", "version": 3,
            "state": {"customerMessage": "My order is late", "history": [{"status": "STARTED", "at": ts(0), "reason": "created"}],
                      "execution": {"steps": [{"step": "refund", "ok": True, "detail": "RF-1 INITIATED"}, {"step": "replacement_order", "ok": False, "detail": "boom"},
                                              {"step": "release_inventory", "ok": True, "detail": "RSV-1"}], "compensated": True}},
            "updatedAt": ts(15000)}
WF_TRACE = {"workflow": WORKFLOW, "history": WORKFLOW["state"]["history"],
            "auditEvents": [{"eventType": "WORKFLOW_STARTED", "agentName": "workflow-service", "actor": "CUST-1001", "createdAt": ts(1), "payload": "{}"}],
            "kafkaEvents": [{"topic": "workflow.started", "event_id": "e1", "type": "WorkflowStarted", "correlation_id": "c1", "causation_id": None,
                             "workflow_id": "WF-TEST000001", "payload": {"orderId": "ORD-7842"}, "created_at": ts(2), "sent_at": ts(3)}]}
APPROVAL = {"approvalId": "APR-TEST000001", "workflowId": "WF-TEST000001", "status": "PENDING", "requestedAction": "REFUND", "amount": 129.99,
            "reason": "Lost shipment", "riskLevel": "MEDIUM", "decidedBy": None,
            "expiresAt": (datetime.now(timezone.utc) + timedelta(minutes=25)).isoformat(), "createdAt": ts(0)}
METRICS = {"windowHours": 24, "totals": {"runs": 2, "tokens": 9000, "costUsd": 0.0018, "byStatus": {"COMPLETED": 2}, "guardrailRejections": 0},
           "runs": [{"workflow_id": "WF-TEST000001", "at": ts(0), "status": "COMPLETED", "tokens": 4500, "cost_usd": 0.0009, "rag_confidence": 0.9,
                     "guardrail_rejections": 0, "node_errors": 0}],
           "rag": {"queries": 100, "avg_ms": 120, "p50_ms": 3, "p95_ms": 700, "cache_hits": 40, "abstained": 20, "degraded": 0, "avg_confidence": 0.7,
                   "cache_hit_rate": 0.4, "abstain_rate": 0.2},
           "ragAbstainedQueries": [{"query_preview": "do you ship to mars", "confidence": 0.2, "created_at": ts(5)}],
           "ragSeries": [{"hour": ts(0), "queries": 50, "abstained": 10, "cache_hits": 20}]}


class FakeApi:
    """Same interface as ApiClient; records the calls the UI makes."""

    def __init__(self):
        self.calls, self.decisions = [], []
        self.fail: set[str] = set()

    def _maybe_fail(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise ApiError(503, "DOWN", f"{name} is down")

    def health(self): return {"auth-service": True, "workflow-service": True, "approval-service": True, "agent-host": False}
    def create_workflow(self, who, order, msg, correlation_id=None): self._maybe_fail("create_workflow"); return {"workflowId": "WF-QUEUED0001"}
    def get_workflow(self, who, wf): self._maybe_fail("get_workflow"); return WORKFLOW
    def list_workflows(self, who, status=None): self._maybe_fail("list_workflows"); return [WORKFLOW]
    def workflow_trace(self, who, wf): self._maybe_fail("workflow_trace"); return WF_TRACE
    def recent_events(self, who, limit=50, topic=None): self._maybe_fail("recent_events"); return WF_TRACE["kafkaEvents"]
    def get_run(self, who, wf): self._maybe_fail("get_run"); return RUN
    def run_trace(self, who, wf): self._maybe_fail("run_trace"); return {"trace": TRACE, "usage": RUN["usage"]}
    def list_approvals(self, who, status=None):
        self._maybe_fail("list_approvals")
        return [APPROVAL] if status == "PENDING" else []
    def decide(self, who, aid, decision, comment="", correlation_id=None): self._maybe_fail("decide"); self.decisions.append((aid, decision, comment)); return {}
    def audit(self, who, wf): return WF_TRACE["auditEvents"]
    def ask_policy(self, who, question, **filters): self._maybe_fail("ask_policy"); self.calls.append(("ask", question, filters)); return RAG
    def admin_metrics(self, who, hours=24): self._maybe_fail("admin_metrics"); return METRICS

    def stream_run(self, who, order, msg, correlation_id=None):
        self._maybe_fail("stream_run")
        yield {"event": "started", "workflowId": "WF-TEST000001"}
        yield {"event": "node", "node": "supervisor", "trace": TRACE[1]}
        yield {"event": "node", "node": "order", "trace": TRACE[2]}
        yield {"event": "interrupt", "waitingFor": "approval"}
        yield {"event": "final", "run": RUN}


def login_response(username):
    users = {"alice": ("CUST-1001", ["CUSTOMER"]), "bob": ("CUST-2002", ["CUSTOMER"]), "olivia": ("", ["OPERATOR"]), "manny": ("", ["MANAGER", "OPERATOR"])}
    if username not in users:
        raise ApiError(401, "INVALID_CREDENTIALS", "Invalid username or password")
    cid, roles = users[username]
    return {"accessToken": f"tok-{username}", "tokenType": "Bearer", "expiresIn": 900, "username": username, "customerId": cid, "roles": roles}


def _login(self, username, password):
    self.calls.append(("login", username))
    if password != "demo-pass":
        raise ApiError(401, "INVALID_CREDENTIALS", "Invalid username or password")
    return login_response(username)


FakeApi.login = _login
