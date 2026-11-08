#!/usr/bin/env python3
"""Runs the golden datasets (evaluation/golden-datasets/*.json) against the running stack (`make up`).

    python3 evaluation/run_golden.py [scenario-name ...]

For every "live" case it creates a fresh order, submits the customer request, lets a manager approve/reject, then checks what the
dataset expects: final status, tools called / not called, approval requirement, audit events, customer message, database rows and
who may see what. "rag" cases ask the policy assistant. Cases that need unit-level hooks (mode component/api/mcp, duplicate Kafka
events) are skipped here; agent-host/tests and the service tests cover them.
"""
import glob
import json
import os
import re
import subprocess
import sys
import time
import uuid

import httpx

TOKEN = os.getenv("MCP_SERVICE_TOKEN", "local-demo-token")
WORKFLOW, APPROVAL, AGENT = "http://localhost:8088", "http://localhost:8085", "http://localhost:8092"
POLICY = "http://localhost:8086"
PERSONA = {"alice": ("CUST-1001", ""), "bob": ("CUST-2002", ""), "olivia": ("OPS-1", "OPERATOR"), "manny": ("MGR-1", "MANAGER,OPERATOR")}
TERMINAL = {"COMPLETED", "PARTIALLY_COMPLETED", "REJECTED", "ESCALATED", "FAILED"}
http = httpx.Client(timeout=120)


def sql(query: str) -> str:
    out = subprocess.run(["docker", "exec", "-i", "platform-postgres", "psql", "-U", "ecommerce", "-d", "ecommerce", "-At", "-c", query],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def headers(who: str, customer: str = "") -> dict:
    cust, roles = PERSONA.get(who, (customer, ""))
    return {"X-Service-Token": TOKEN, "X-Customer-Id": cust, "X-Roles": roles, "X-Correlation-Id": f"eval-{uuid.uuid4().hex[:8]}"}


def wait_run(wf: str, cust: str, until, timeout=180) -> dict:
    run, end = {}, time.time() + timeout
    while time.time() < end:
        r = http.get(f"{AGENT}/api/agent/runs/{wf}", headers=headers("service", cust))
        if r.status_code == 200:
            run = r.json()
            if until(run):
                break
        time.sleep(2)
    return run


def make_order(o: dict) -> str:
    oid = "ORD-EV" + uuid.uuid4().hex[:8].upper()
    sql(f"INSERT INTO customer_order (order_id, customer_id, status, payment_status, total_amount, product_id, product_name) "
        f"VALUES ('{oid}', '{o['customer']}', 'SHIPPED', 'CAPTURED', {o['amount']}, '{o['product']}', 'Eval product')")
    if o.get("shipment"):
        s = o["shipment"]
        sql(f"INSERT INTO shipment (order_id, customer_id, carrier, tracking_no, status, expected_delivery, delay_days, last_event) "
            f"VALUES ('{oid}', '{o['customer']}', 'EvalCarrier', 'TRK-{oid[-6:]}', '{s['status']}', CURRENT_DATE - {s['delay_days']}, {s['delay_days']}, 'eval')")
    sql("UPDATE product SET available_qty = 25, reserved_qty = 0 WHERE product_id IN ('PRD-101', 'PRD-103')")
    return oid


def run_live(case: dict) -> dict:
    setup, inp = case["setup"], case["input"]
    oid = make_order(setup["order"])
    actor, cust = inp.get("actor", "service"), inp["customerId"]
    poison = None
    if setup.get("poison_policy"):
        r = http.post(f"{POLICY}/api/policies/ingest?wait=true", headers=headers("service") | {"X-Roles": "ADMIN"}, json={
            "policyKey": "eval-poison-" + uuid.uuid4().hex[:6], "version": "1", "title": "Refund addendum",
            "content": setup["poison_policy"], "contentType": "text/markdown", "policyType": "refund"})
        poison = r.json().get("documentId")
    try:
        r = http.post(f"{WORKFLOW}/api/workflows", headers=headers(actor, cust), json={
            "orderId": oid, "message": inp["message"].format(order=oid), "requestId": str(uuid.uuid4())})
        obs = {"creation_status": r.status_code, "tools": set(), "audit": set(), "topics": set(), "errors": [], "message": ""}
        if r.status_code >= 400:
            obs |= {"status": "DENIED_AT_CREATION", "approval": False, "errors": [r.json().get("code", "") if r.content else ""], "refund_rows": int(sql(f"SELECT count(*) FROM payment_refund WHERE order_id = '{oid}'"))}
            return obs
        wf = r.json()["workflowId"]
        run = wait_run(wf, cust, lambda x: x["waitingForApproval"] or x["status"] in TERMINAL)
        if run.get("waitingForApproval") and setup.get("before_approval_sql"):
            sql(setup["before_approval_sql"])                       # e.g. the replacement product sells out while waiting for the manager
        if run.get("waitingForApproval") and setup.get("approval") in ("APPROVE", "REJECT"):
            http.post(f"{APPROVAL}/api/approvals/{run['approvalId']}/decision", headers=headers("manny"), json={
                "decision": "APPROVED" if setup["approval"] == "APPROVE" else "REJECTED", "decidedBy": "manny"}).raise_for_status()
        run = wait_run(wf, cust, lambda x: x["status"] in TERMINAL and x.get("customerResponse"))
        time.sleep(1.5)                                             # let late audit writes land
        trace = http.get(f"{AGENT}/api/agent/runs/{wf}/trace", headers=headers("service", cust)).json()
        wtrace = http.get(f"{WORKFLOW}/api/workflows/{wf}/trace", headers=headers("olivia")).json()
        audit = {a["eventType"] for a in wtrace.get("auditEvents", [])}
        policy = run.get("policy") or {}
        obs |= {"status": run.get("status"), "intent": run.get("intent"), "approval": bool(run.get("approvalId")), "message": run.get("customerResponse") or "",
                "errors": list(run.get("errors", [])), "audit": audit, "topics": {e["topic"] for e in wtrace.get("kafkaEvents", [])},
                "tools": {c["tool"] for t in trace.get("trace", []) for c in t.get("tool_calls", [])} | {e[5:].lower() for e in audit if e.startswith("TOOL_")},
                "rag": {"confidence": policy.get("rag_confidence", 0), "grounded": bool(policy.get("grounded")),
                        "citations": [{"policyKey": c["policy_key"], "version": c["version"]} for c in policy.get("citations", [])]},
                "refund_rows": int(sql(f"SELECT count(*) FROM payment_refund WHERE workflow_id = '{wf}'")),
                "approval_rows": int(sql(f"SELECT count(*) FROM approval_request WHERE workflow_id = '{wf}'")),
                "refund_amount_max": float(sql(f"SELECT coalesce(max(amount), 0) FROM payment_refund WHERE workflow_id = '{wf}'") or 0),
                "denials": [(d["actor"], d["resource"], http.get({"workflow": f"{WORKFLOW}/api/workflows/{wf}", "run": f"{AGENT}/api/agent/runs/{wf}",
                                                               "trace": f"{WORKFLOW}/api/workflows/{wf}/trace"}[d["resource"]], headers=headers(d["actor"])).status_code)
                            for d in case.get("access_denials", [])]}
        return obs
    finally:
        if poison:
            http.post(f"{POLICY}/api/policies/{poison}/archive", headers=headers("service") | {"X-Roles": "ADMIN"})


def run_rag(case: dict) -> dict:
    j = http.post(f"{AGENT}/api/agent/rag/ask", headers=headers("service", "EVAL"), json={"question": case["rag_question"]}).json()
    return {"message": j["answer"], "tools": set(), "audit": set(), "topics": set(), "errors": [],
            "rag": {"confidence": j["confidence"], "grounded": j["grounded"], "citations": [{"policyKey": s["policyKey"], "version": s["version"]} for s in j["sources"]]}}


def check(case: dict, o: dict) -> list[tuple[str, bool]]:
    g, t, msg = case.get("expected_guardrail_behavior") or {}, case.get("expected_tools") or {}, o["message"]
    c = []
    if case.get("expected_final_status"):
        c.append((f"final status {case['expected_final_status']} (got {o.get('status')})", o.get("status") == case["expected_final_status"]))
    if case.get("expected_approval_required") is not None:
        c.append((f"approval required = {case['expected_approval_required']}", o.get("approval") == case["expected_approval_required"]))
    c += [(f"calls {x}", x in o["tools"]) for x in t.get("must_call", [])] + [(f"never calls {x}", x not in o["tools"]) for x in t.get("must_not_call", [])]
    c += [(f"audit has {x}", x in o["audit"]) for x in g.get("audit_events_present", [])] + [(f"audit lacks {x}", x not in o["audit"]) for x in g.get("audit_events_absent", [])]
    c += [(f"kafka topic {x}", x in o["topics"]) for x in g.get("kafka_topics_present", [])]
    c += [(f"message has '{x}'", x.lower() in msg.lower()) for x in g.get("message_includes", [])]
    if g.get("message_includes_any"):
        c.append((f"message has one of {g['message_includes_any']}", any(x.lower() in msg.lower() for x in g["message_includes_any"])))
    c += [(f"message lacks '{x}'", x.lower() not in msg.lower()) for x in g.get("message_excludes", [])]
    c += [(f"message matches /{x}/", re.search(x, msg, re.I) is not None) for x in g.get("message_matches", [])]
    c += [(f"message avoids /{x}/", re.search(x, msg, re.I) is None) for x in g.get("message_must_not_match", [])]
    c += [(f"error {x}", any(x in e for e in o["errors"])) for x in g.get("errors_include", [])]
    if case.get("expected_rag_confidence"):
        r, rag = case["expected_rag_confidence"], o.get("rag", {})
        c.append((f"rag confidence {r.get('min', 0)}-{r.get('max', 1)} (got {rag.get('confidence')})", r.get("min", 0) <= rag.get("confidence", 0) <= r.get("max", 1)))
        if "grounded" in r:
            c.append((f"rag grounded = {r['grounded']}", rag.get("grounded") == r["grounded"]))
    c += [(f"cites {x['policyKey']}", any(y["policyKey"] == x["policyKey"] and x.get("version") in (None, y["version"]) for y in o.get("rag", {}).get("citations", [])))
          for x in case.get("expected_citations", [])]
    for key, want in (case.get("expected_state") or {}).items():
        got = o.get(key)
        c.append((f"{key} = {want} (got {got})", (got is None or got <= want + 1e-9) if key == "refund_amount_max" else got == want))
    c += [(f"{a} gets {s} on {r}", (a, r, s) in o.get("denials", [])) for a, r, s in ((d["actor"], d["resource"], d["status"]) for d in case.get("access_denials", []))]
    return c


def main() -> int:
    wanted, failed = set(sys.argv[1:]), 0
    for path in sorted(glob.glob(os.path.join(os.path.dirname(__file__), "golden-datasets", "*.json"))):
        data = json.load(open(path))
        if wanted and data["scenario"] not in wanted:
            continue
        for case in data["cases"]:
            name = f"{data['scenario']:28} {case['id']:34}"
            if case["mode"] not in ("live", "rag") or case.get("setup", {}).get("duplicate_events"):
                print(f"SKIP  {name} (mode {case['mode']}: covered by unit tests)")
                continue
            t0 = time.time()
            results = check(case, run_live(case) if case["mode"] == "live" else run_rag(case))
            bad = [n for n, ok in results if not ok]
            failed += bool(bad)
            print(f"{'FAIL' if bad else 'PASS'}  {name} {len(results) - len(bad)}/{len(results)} checks {time.time() - t0:.0f}s")
            for n in bad:
                print(f"      x {n}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
