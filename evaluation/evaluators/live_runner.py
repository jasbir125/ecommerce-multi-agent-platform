"""Runs a test case against the real, running stack (Spring services, MCP servers, agent host, Kafka, Postgres, a real model).

Each case gets a fresh order. Identity is real: customers act with their own JWT, approvals are decided by the manager's JWT.
Behaviour is observed from the places the system records it: the agent run and its trace, the workflow-service audit trail
and Kafka events, and the database (refund / approval rows).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent-host"))

from .checks import Observed  # noqa: E402

URL = {"order": "http://localhost:8081", "shipment": "http://localhost:8082", "payment": "http://localhost:8083", "inventory": "http://localhost:8084",
       "approval": "http://localhost:8085", "policy": "http://localhost:8086", "audit": "http://localhost:8087", "workflow": "http://localhost:8088",
       "agent": "http://localhost:8092", "auth": "http://localhost:8093"}
SERVICE_TOKEN = os.getenv("MCP_SERVICE_TOKEN", "local-demo-token")
DSN = os.getenv("EVAL_DATABASE_URL", f"postgresql://ecommerce:ecommerce_local_pw@localhost:{os.getenv('POSTGRES_HOST_PORT', '5432')}/ecommerce")
KAFKA = os.getenv("EVAL_KAFKA", "localhost:9092")
TERMINAL = {"COMPLETED", "PARTIALLY_COMPLETED", "REJECTED", "ESCALATED", "FAILED"}


class LiveEnv:
    async def prepare(self) -> None:
        self.http = httpx.AsyncClient(timeout=60)
        self.db = await asyncpg.connect(DSN)
        self.tokens: dict[str, str] = {}
        for user in ("alice", "bob", "olivia", "manny"):
            r = await self.http.post(f"{URL['auth']}/api/auth/token", json={"username": user, "password": os.getenv("AUTH_DEMO_PASSWORD", "demo-pass")})
            r.raise_for_status()
            self.tokens[user] = r.json()["accessToken"]
        # replacement stock is consumed by every completed run: top it up
        await self.db.execute("UPDATE product SET available_qty = 25, reserved_qty = 0 WHERE product_id = 'PRD-101'")
        await self.db.execute("UPDATE product SET available_qty = 40, reserved_qty = 0 WHERE product_id = 'PRD-103'")

    async def close(self) -> None:
        await self.http.aclose()
        await self.db.close()

    # ------------------------------------------------------------------ helpers
    def svc(self, customer: str | None = None, roles: str | None = None) -> dict[str, str]:
        h = {"X-Service-Token": SERVICE_TOKEN, "Content-Type": "application/json"}
        if customer:
            h["X-Customer-Id"] = customer
        if roles:
            h["X-Roles"] = roles
        return h

    def jwt(self, user: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[user]}", "Content-Type": "application/json"}

    def actor_headers(self, actor: str, customer: str) -> dict[str, str]:
        return self.jwt(actor.split(":", 1)[1]) if actor.startswith("jwt:") else self.svc(customer)

    async def make_order(self, cfg: dict[str, Any]) -> str:
        order_id = "ORD-EV" + uuid.uuid4().hex[:8].upper()
        await self.db.execute("INSERT INTO customer_order (order_id, customer_id, status, payment_status, total_amount, product_id, product_name) "
                              "VALUES ($1, $2, 'SHIPPED', 'CAPTURED', $3, $4, 'Eval product')", order_id, cfg["customer"], cfg["amount"], cfg["product"])
        ship = cfg.get("shipment")
        if ship:
            await self.db.execute("INSERT INTO shipment (order_id, customer_id, carrier, tracking_no, status, expected_delivery, delay_days, last_event) "
                                  "VALUES ($1, $2, 'EvalCarrier', $3, $4, CURRENT_DATE - $5::int, $5, 'eval')", order_id, cfg["customer"], "TRK-" + order_id[-6:], ship["status"], ship["delay_days"])
        return order_id

    async def add_fault(self, f: dict[str, Any], workflow_id: str | None, order_id: str) -> None:
        scope = workflow_id if f["scope"] == "workflow" else order_id
        await self.db.execute("INSERT INTO fault_injection (service, operation, scope, mode, remaining) VALUES ($1, $2, $3, $4, $5)",
                              f["service"], f["operation"], scope, f.get("mode", "error"), f.get("remaining", 1))

    async def replay(self, topic: str, workflow_id: str, extra_copies: int, new_id_copy: bool = False) -> None:
        """Publish copies of an event this system already produced (same event id = a duplicate delivery)."""
        from aiokafka import AIOKafkaProducer
        row = None
        for _ in range(40):
            row = await self.db.fetchrow("SELECT payload, trace_context FROM outbox_event WHERE topic = $1 AND payload::jsonb->>'workflowId' = $2 ORDER BY id LIMIT 1", topic, workflow_id)
            if row and True:
                break
            await asyncio.sleep(0.5)
        if not row:
            raise RuntimeError(f"no {topic} event found for {workflow_id}")
        producer = AIOKafkaProducer(bootstrap_servers=KAFKA)
        await producer.start()
        try:
            payloads = [row["payload"]] * extra_copies
            if new_id_copy:
                env = json.loads(row["payload"])
                env["eventId"] = str(uuid.uuid4())
                payloads.append(json.dumps(env))
            for p in payloads:
                await producer.send_and_wait(topic, p.encode(), key=workflow_id.encode())
        finally:
            await producer.stop()

    async def wait_run(self, wf: str, customer: str, until, timeout: float = 180) -> dict[str, Any]:
        end, run = time.time() + timeout, {}
        while time.time() < end:
            r = await self.http.get(f"{URL['agent']}/api/agent/runs/{wf}", headers=self.svc(customer))
            if r.status_code == 200:
                run = r.json()
                if until(run):
                    return run
            await asyncio.sleep(2)
        return run

    # ------------------------------------------------------------------------ run
    async def run(self, case: dict[str, Any]) -> Observed:
        mode = case["mode"]
        if mode == "rag":
            return await self._rag(case)
        setup = case.get("setup", {})
        order_id = await self.make_order(setup["order"])
        if mode == "api":
            return await self._api(case, order_id)
        if mode == "mcp":
            return await self._mcp(case, order_id)
        poison_doc = None
        try:
            if setup.get("poison_policy"):
                poison_doc = await self._ingest_poison(setup["poison_policy"])
            return await self._workflow(case, order_id)
        finally:
            if poison_doc:
                await self.http.post(f"{URL['policy']}/api/policies/{poison_doc}/archive", headers=self.svc(roles="ADMIN"))

    async def _ingest_poison(self, text: str) -> int:
        r = await self.http.post(f"{URL['policy']}/api/policies/ingest?wait=true", headers=self.svc(roles="ADMIN"), json={
            "policyKey": "eval-poison-" + uuid.uuid4().hex[:6], "version": "1", "title": "Refund addendum", "content": text, "contentType": "text/markdown", "policyType": "refund"})
        r.raise_for_status()
        return r.json()["documentId"]

    async def _workflow(self, case: dict[str, Any], order_id: str) -> Observed:
        setup, inp = case["setup"], case["input"]
        customer = setup["order"]["customer"]
        o = Observed()
        message = inp["message"].format(order=order_id)
        headers = self.actor_headers(inp.get("actor", "service"), customer)
        body = {"orderId": order_id, "message": message, "requestId": str(uuid.uuid4())}
        r = await self.http.post(f"{URL['workflow']}/api/workflows", headers=headers, json=body)
        o.state["creation_status"] = r.status_code
        if r.status_code >= 400:
            o.final_status = "DENIED_AT_CREATION"
            try:
                code = r.json().get("code") or r.json().get("detail", {}).get("code", "")
            except ValueError:
                code = ""
            o.errors.append(code)
            o.approval_required = False
            o.state["refund_rows"] = await self.db.fetchval("SELECT count(*) FROM payment_refund WHERE order_id = $1", order_id)
            return o
        wf = r.json()["workflowId"]
        for f in setup.get("faults", []):
            await self.add_fault(f, wf, order_id)
        dups = setup.get("duplicate_events", {})
        if "workflow.started" in dups:
            await self.replay("workflow.started", wf, dups["workflow.started"] - 1, new_id_copy=True)

        run = await self.wait_run(wf, customer, lambda x: x["waitingForApproval"] or x["status"] in TERMINAL)
        decision = setup.get("approval", "NONE")
        if run.get("waitingForApproval") and decision in ("APPROVE", "REJECT"):
            d = await self.http.post(f"{URL['approval']}/api/approvals/{run['approvalId']}/decision", headers=self.jwt("manny"),
                                     json={"decision": "APPROVED" if decision == "APPROVE" else "REJECTED", "decidedBy": "manny"})
            d.raise_for_status()
            if "workflow.approval.decided" in dups:
                await self.replay("workflow.approval.decided", wf, dups["workflow.approval.decided"] - 1)
        run = await self.wait_run(wf, customer, lambda x: x["status"] in TERMINAL and x.get("customerResponse"))
        await asyncio.sleep(1.5)                                     # let late events and audit writes land
        for den in case.get("access_denials", []):
            path = {"workflow": f"{URL['workflow']}/api/workflows/{wf}", "run": f"{URL['agent']}/api/agent/runs/{wf}", "trace": f"{URL['workflow']}/api/workflows/{wf}/trace"}[den["resource"]]
            rr = await self.http.get(path, headers=self.jwt(den["actor"].split(":", 1)[1]))
            o.access.append({"actor": den["actor"], "resource": den["resource"], "status": rr.status_code})
        return await self._collect(o, wf, order_id, customer, run)

    async def _collect(self, o: Observed, wf: str, order_id: str, customer: str, run: dict[str, Any]) -> Observed:
        o.intent, o.final_status = run.get("intent"), run.get("status")
        o.approval_required = bool(run.get("approvalId"))
        o.errors, o.message = list(run.get("errors", [])), run.get("customerResponse") or ""
        pol = run.get("policy") or {}
        o.rag = {"confidence": pol.get("rag_confidence", 0.0), "grounded": bool(pol.get("grounded") and not pol.get("insufficient_evidence")),
                 "citations": [{"policyKey": c["policy_key"], "version": c["version"]} for c in pol.get("citations", [])]}
        tr = (await self.http.get(f"{URL['agent']}/api/agent/runs/{wf}/trace", headers=self.svc(customer))).json()
        o.tools = {c["tool"] for t in tr.get("trace", []) for c in t.get("tool_calls", [])}
        wt = (await self.http.get(f"{URL['workflow']}/api/workflows/{wf}/trace", headers=self.jwt("olivia"))).json()
        o.audit_events = {a["eventType"] for a in wt.get("auditEvents", [])}
        o.tools |= {e[5:].lower() for e in o.audit_events if e.startswith("TOOL_")}
        o.kafka_topics = {e["topic"] for e in wt.get("kafkaEvents", [])}
        o.state["agent_runs"] = sum(1 for t in tr.get("trace", []) if t["agent"] == "input_guard")
        o.state["refund_rows"] = await self.db.fetchval("SELECT count(*) FROM payment_refund WHERE workflow_id = $1", wf)
        o.state["approval_rows"] = await self.db.fetchval("SELECT count(*) FROM approval_request WHERE workflow_id = $1", wf)
        amt = await self.db.fetchval("SELECT max(amount) FROM payment_refund WHERE workflow_id = $1", wf)
        o.state["refund_amount_max"] = float(amt) if amt is not None else None
        return o

    # ------------------------------------------------------------- direct probes
    async def _api(self, case: dict[str, Any], order_id: str) -> Observed:
        o = Observed()
        customer = case["setup"]["order"]["customer"]
        for i, step in enumerate(case["api_steps"], 1):
            fmt = lambda v: json.loads(json.dumps(v).replace("{order}", order_id)) if not isinstance(v, str) else v.replace("{order}", order_id)  # noqa: E731
            r = await self.http.request(step["method"], URL[step["service"]] + fmt(step["path"]), headers=self.svc(customer), json=fmt(step.get("body")))
            code = (r.json().get("code") if r.content else "") or ""
            o.step_checks.append((f"api step {i}: {step['method']} {step['service']}{step['path']} -> {step['expect_status']}", r.status_code == step["expect_status"], f"got {r.status_code} {code}"))
            if step.get("expect_code"):
                o.step_checks.append((f"api step {i}: error code {step['expect_code']}", code == step["expect_code"], f"got {code}"))
        o.state["refund_rows"] = await self.db.fetchval("SELECT count(*) FROM payment_refund WHERE order_id = $1", order_id)
        return o

    async def _mcp(self, case: dict[str, Any], order_id: str) -> Observed:
        from app.config import Settings
        from app.guardrails.tool_guardrails import ToolCallError
        from app.mcp.client import McpGateway
        o = Observed()
        customer = case["setup"]["order"]["customer"]
        gw = McpGateway(Settings(), customer, "WF-EVAL000001")
        for i, step in enumerate(case["mcp_steps"], 1):
            args = {k: (v.replace("{order}", order_id) if isinstance(v, str) else v) for k, v in step["args"].items()}
            try:
                await gw.call(step["tool"], args)
                o.step_checks.append((f"mcp step {i}: {step['tool']} refused", False, "the call succeeded"))
            except ToolCallError as e:
                o.step_checks.append((f"mcp step {i}: {step['tool']} refused with {step['expect_error_contains']}", step["expect_error_contains"] in str(e), str(e)[:100]))
        o.state["refund_rows"] = await self.db.fetchval("SELECT count(*) FROM payment_refund WHERE order_id = $1", order_id)
        return o

    async def _rag(self, case: dict[str, Any]) -> Observed:
        r = await self.http.post(f"{URL['agent']}/api/agent/rag/ask", headers=self.svc("EVAL-LIVE"), json={"question": case["rag_question"]})
        r.raise_for_status()
        j = r.json()
        o = Observed(message=j["answer"])
        o.rag = {"confidence": j["confidence"], "grounded": j["grounded"], "citations": [{"policyKey": s["policyKey"], "version": s["version"]} for s in j["sources"]]}
        return o
