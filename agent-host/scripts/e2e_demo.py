"""Live end-to-end demo of the delayed-shipment scenario through the real stack (needs OPENAI/ANTHROPIC key).

    python agent-host/scripts/e2e_demo.py [--order ORD-7842] [--message "..."]

Flow: create workflow (workflow-service) -> Kafka -> agent host investigates -> waits for approval ->
a manager approves -> Kafka -> agent host executes refund + replacement -> customer notified.
"""
import argparse
import json
import os
import time
import uuid

import httpx

TOKEN = os.getenv("MCP_SERVICE_TOKEN", "local-demo-token")
AGENT = os.getenv("AGENT_HOST_URL", "http://localhost:8092")
WORKFLOW = os.getenv("WORKFLOW_SERVICE_URL", "http://localhost:8088")
APPROVAL = os.getenv("APPROVAL_SERVICE_URL", "http://localhost:8085")
CUSTOMER = os.getenv("DEMO_CUSTOMER", "CUST-1001")
H = {"X-Service-Token": TOKEN, "X-Customer-Id": CUSTOMER}

ap = argparse.ArgumentParser()
ap.add_argument("--order", default="ORD-7842")
ap.add_argument("--message", default="My order ORD-7842 has been delayed by five days. Cancel the order, refund me, and suggest a cheaper replacement.")
ap.add_argument("--reject", action="store_true", help="reject the approval instead of approving it")
args = ap.parse_args()


def wait_for(pred, what, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        r = httpx.get(f"{AGENT}/api/agent/runs/{wf}", headers=H)
        if r.status_code == 200 and pred(r.json()):
            return r.json()
        time.sleep(2)
    raise SystemExit(f"timed out waiting for {what}")


wf = httpx.post(f"{WORKFLOW}/api/workflows", headers=H, json={
    "orderId": args.order, "message": args.message, "requestId": str(uuid.uuid4())}).json()["workflowId"]
print("workflow", wf)

run = wait_for(lambda r: r["waitingForApproval"] or r["status"] in ("ESCALATED", "FAILED"), "approval request")
print("status:", run["status"], "| plan:", run["plan"])
print("order:", run["order"] and (run["order"]["status"], run["order"]["total_amount"]))
print("shipment:", run["shipment"] and (run["shipment"]["status"], run["shipment"]["delay_days"], run["shipment"]["exception_type"]))
print("policy:", run["policy"] and (run["policy"]["policy_key"], "v" + str(run["policy"]["policy_version"]), "eligible=", run["policy"]["refund_eligible"], "conf=", run["policy"]["rag_confidence"]))
print("product:", run["product"] and (run["product"]["product_id"], run["product"]["price"]))
print("risk:", run["risk"] and (run["risk"]["level"], run["risk"]["factors"]))
print("resolution:", run["resolution"] and (run["resolution"]["action"], run["resolution"]["refund_amount"], run["resolution"]["replacement_product_id"]))
if not run["waitingForApproval"]:
    print("customer response:", run["customerResponse"])
    raise SystemExit(0)

print("approval", run["approvalId"], "is PENDING; nothing has been executed yet")
decision = "REJECTED" if args.reject else "APPROVED"
d = httpx.post(f"{APPROVAL}/api/approvals/{run['approvalId']}/decision", headers={**H, "X-Roles": "MANAGER"},
               json={"decision": decision, "decidedBy": "manager-1"}).json()
print("manager decision:", d["status"])

final = wait_for(lambda r: r["status"] in ("COMPLETED", "PARTIALLY_COMPLETED", "REJECTED", "ESCALATED", "FAILED") and r["customerResponse"], "completion")
print("\nFINAL STATUS:", final["status"])
if final["execution"]:
    ex = final["execution"]
    print("refund:", ex["refund_id"], ex["refund_status"], "| replacement:", ex["replacement_order_id"], "| carrier case:", ex["carrier_case_id"])
print("customer response:", final["customerResponse"])
print("errors:", final["errors"])
print("usage:", {k: final["usage"].get(k) for k in ("total_tokens", "cost_usd")})
tr = httpx.get(f"{AGENT}/api/agent/runs/{wf}/trace", headers=H).json()
print("\ntimeline:")
for t in tr["trace"]:
    print(f"  {t['agent']:<18} {t['status']:<6} {t['latency_ms']:>6} ms  tools={[c['tool'] for c in t.get('tool_calls', [])]}")
wf_state = httpx.get(f"{WORKFLOW}/api/workflows/{wf}", headers={**H, "X-Roles": "OPERATOR"}).json()
print("\nworkflow-service status:", wf_state["status"])
audit = httpx.get(f"http://localhost:8087/api/audit/workflows/{wf}", headers=H).json()
print("audit events:", len(audit))
