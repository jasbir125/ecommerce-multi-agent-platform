"""Starts, streams and resumes workflow runs, with per-workflow locking so a run never executes twice."""
from __future__ import annotations

import logging
import uuid
from typing import Any, AsyncIterator

import httpx
from langgraph.types import Command

from app.config import Settings
from app.graph.deps import Deps
from app.guardrails.input_guardrails import RunRateLimiter
from app.models.workflow_state import WorkflowState

log = logging.getLogger("agent.runner")
TERMINAL = {"COMPLETED", "PARTIALLY_COMPLETED", "REJECTED", "ESCALATED", "FAILED"}


class RunError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


def summarize(values: dict[str, Any], next_nodes: tuple = ()) -> dict[str, Any]:
    """Compact, JSON-friendly view of a run for the API and UI."""
    def dump(x):
        return x.model_dump() if hasattr(x, "model_dump") else x
    return {
        "workflowId": values.get("workflow_id"), "customerId": values.get("customer_id"), "orderId": values.get("order_id"),
        "status": values.get("status"), "intent": values.get("intent"), "plan": values.get("plan", []),
        "waitingForApproval": "approval_wait" in next_nodes, "approvalId": values.get("approval_id"),
        "approvalStatus": values.get("approval_status"),
        "order": dump(values.get("order")), "shipment": dump(values.get("shipment")), "policy": dump(values.get("policy")),
        "product": dump(values.get("product_recommendation")), "risk": dump(values.get("risk")),
        "resolution": dump(values.get("resolution")), "execution": dump(values.get("execution")),
        "customerResponse": values.get("customer_response"), "errors": values.get("errors", []),
        "evidence": values.get("evidence", []), "usage": values.get("usage", {}), "traceEntries": len(values.get("trace", [])),
    }


class RunManager:
    def __init__(self, settings: Settings, deps: Deps, graph, redis=None):
        self.s, self.deps, self.graph, self.redis = settings, deps, graph, redis
        self._local_locks: set[str] = set()
        self.limiter = RunRateLimiter(settings.runs_per_minute_per_customer)
        self.ask_limiter = RunRateLimiter(settings.rag_ask_per_minute)

    # ------------------------------------------------------------------ locking
    async def _acquire(self, key: str, ttl: int = 900) -> bool:
        if self.redis is not None:
            try:
                return bool(await self.redis.set(f"agent:{key}", "1", nx=True, ex=ttl))
            except Exception as e:  # noqa: BLE001
                log.warning("redis lock unavailable, using process-local lock: %s", e)
        if key in self._local_locks:
            return False
        self._local_locks.add(key)
        return True

    async def _release(self, key: str) -> None:
        self._local_locks.discard(key)
        if self.redis is not None:
            try:
                await self.redis.delete(f"agent:{key}")
            except Exception:  # noqa: BLE001
                pass

    async def claim_event(self, event_id: str) -> bool:
        """True the first time this Kafka event id is seen (consumer-side idempotency)."""
        return await self._acquire(f"event:{event_id}", ttl=3600)

    # ------------------------------------------------------------------- config
    def _config(self, workflow_id: str, customer_id: str, order_id: str) -> dict:
        ctx = self.deps.ensure(workflow_id, customer_id, order_id)
        return {"configurable": {"thread_id": workflow_id, "deps": self.deps}, "callbacks": [ctx.usage], "recursion_limit": 60}

    async def snapshot(self, workflow_id: str):
        return await self.graph.aget_state({"configurable": {"thread_id": workflow_id}})

    # -------------------------------------------------------------------- start
    async def stream_start(self, customer_id: str, order_id: str, message: str,
                           workflow_id: str | None = None, request_id: str | None = None) -> AsyncIterator[dict]:
        if not self.limiter.allow(customer_id):
            raise RunError(429, "RATE_LIMITED", "Too many requests for this customer; try again in a minute")
        if workflow_id is None:
            try:
                wf = await self.deps.workflows.create(customer_id, order_id, message, request_id or str(uuid.uuid4()))
            except httpx.HTTPStatusError as e:      # e.g. input guardrails or ownership refused by workflow-service
                try:
                    code = e.response.json().get("code", "UPSTREAM_ERROR")
                except ValueError:
                    code = "UPSTREAM_ERROR"
                raise RunError(e.response.status_code, code, "workflow-service refused the request") from e
            workflow_id = wf["workflowId"]
        if not await self._acquire(f"run:{workflow_id}"):
            raise RunError(409, "RUN_IN_PROGRESS", f"Workflow {workflow_id} is already running or has run")
        try:
            if (await self.snapshot(workflow_id)).values:
                raise RunError(409, "ALREADY_STARTED", f"Workflow {workflow_id} already has a run")
            state = WorkflowState(workflow_id=workflow_id, request_id=request_id or "", customer_id=customer_id,
                                  order_id=order_id, customer_message=message)
            yield {"event": "started", "workflowId": workflow_id}
            async for ev in self._drive(state, self._config(workflow_id, customer_id, order_id)):
                yield ev
        finally:
            await self._release(f"run:{workflow_id}")

    async def start(self, **kw) -> dict:
        final = {}
        async for ev in self.stream_start(**kw):
            if ev["event"] == "final":
                final = ev["run"]
        return final

    async def start_from_workflow(self, workflow_id: str, customer_id: str) -> dict | None:
        """Kafka path: a customer request was already created in workflow-service."""
        wf = await self.deps.workflows.get(workflow_id, customer_id)
        if wf["status"] != "STARTED":
            log.info("workflow %s is %s, not starting", workflow_id, wf["status"])
            return None
        try:
            return await self.start(customer_id=wf["customerId"], order_id=wf["orderId"],
                                    message=wf["state"].get("customerMessage", ""), workflow_id=workflow_id,
                                    request_id=wf["requestId"])
        except RunError as e:
            log.info("not starting %s: %s", workflow_id, e)
            return None

    # ------------------------------------------------------------------- resume
    async def stream_resume(self, workflow_id: str) -> AsyncIterator[dict]:
        snap = await self.snapshot(workflow_id)
        if not snap.values:
            raise RunError(404, "RUN_NOT_FOUND", f"No run for workflow {workflow_id}")
        if not snap.next:
            yield {"event": "final", "run": summarize(snap.values, snap.next)}
            return
        if not await self._acquire(f"run:{workflow_id}"):
            raise RunError(409, "RUN_IN_PROGRESS", "This workflow is already being processed")
        try:
            v = snap.values
            cfg = self._config(workflow_id, v["customer_id"], v["order_id"])
            async for ev in self._drive(Command(resume="decided"), cfg):
                yield ev
        finally:
            await self._release(f"run:{workflow_id}")

    async def resume(self, workflow_id: str) -> dict:
        final = {}
        async for ev in self.stream_resume(workflow_id):
            if ev["event"] == "final":
                final = ev["run"]
        return final

    # ------------------------------------------------------------------- engine
    async def _drive(self, graph_input, cfg: dict) -> AsyncIterator[dict]:
        wf = cfg["configurable"]["thread_id"]
        async for chunk in self.graph.astream(graph_input, cfg, stream_mode="updates"):
            for node, update in chunk.items():
                if node == "__interrupt__":
                    yield {"event": "interrupt", "waitingFor": "approval"}
                    continue
                entry = (update or {}).get("trace", [None])[-1] if isinstance(update, dict) else None
                yield {"event": "node", "node": node, "trace": entry}
        snap = await self.snapshot(wf)
        run = summarize(snap.values, snap.next)
        if not snap.next:
            await self._finish(wf, snap)
        yield {"event": "final", "run": run}

    async def _finish(self, workflow_id: str, snap) -> None:
        ctx = self.deps.runs.get(workflow_id)
        if self.deps.evaluator:
            await self.deps.evaluator.record(WorkflowState(**snap.values), ctx.usage.summary() if ctx else {})
        self.deps.release(workflow_id)

    # --------------------------------------------------------------------- read
    async def get(self, workflow_id: str) -> dict:
        snap = await self.snapshot(workflow_id)
        if not snap.values:
            raise RunError(404, "RUN_NOT_FOUND", f"No run for workflow {workflow_id}")
        return summarize(snap.values, snap.next)

    async def trace(self, workflow_id: str) -> dict:
        snap = await self.snapshot(workflow_id)
        if not snap.values:
            raise RunError(404, "RUN_NOT_FOUND", f"No run for workflow {workflow_id}")
        return {"workflowId": workflow_id, "status": snap.values.get("status"), "trace": snap.values.get("trace", []),
                "usage": snap.values.get("usage", {})}
