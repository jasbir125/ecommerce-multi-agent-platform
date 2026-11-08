"""Drives the real UI (headless) against the running stack: a customer submits, a manager approves, an operator inspects.

    python streamlit-ui/scripts/live_smoke.py [--order ORD-8801]

Uses real model calls (about 0.1 cent). Needs the stack up and a lost-shipment order that has not been refunded yet.
"""
import argparse
import sys
import time
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from api_client import ApiClient, Identity  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--order", default="ORD-8801")
ap.add_argument("--reject", action="store_true")
args = ap.parse_args()
APP = str(ROOT / "app.py")
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(name)


def session(persona: str, page: str | None = None, **state) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=120)
    at.session_state["persona"] = persona
    if page:
        at.session_state["page"] = page
    for k, v in state.items():
        at.session_state[k] = v
    return at


labels = lambda at: {m.label for m in at.metric}  # noqa: E731

# 1. customer submits a live request from the UI
at = session("customer").run()
at.text_input[0].set_value("CUST-1001")
at.text_input[1].set_value(args.order)
at.text_area[0].set_value(f"My order {args.order} has been delayed by five days. Cancel the order, refund me, and suggest a cheaper replacement.")
t0 = time.time()
at.button[0].click().run()
wf = at.session_state["workflow_id"]
check("customer request creates a workflow and renders", not at.exception and wf.startswith("WF-"), f"{wf} in {time.time() - t0:.0f}s")
check("paused run is explained", any("Waiting for a manager" in w.value for w in at.warning))

api = ApiClient()
manager = Identity("manager", None, "manager-1")
run = api.get_run(manager, wf)
check("agents paused for approval and executed nothing", run["waitingForApproval"] and run["execution"] is None, run["status"])
check("RAG evidence is stored on the run", bool(run["policy"]["rag"]["sources"]) and bool(run["policy"]["rag"]["diagnostics"]["candidates"]))

# 2. manager decides from the Approvals page
at = session("manager", "Approvals").run()
decision = "Reject" if args.reject else "Approve"
check("approval is listed for the manager", not at.exception and any(b.label == decision for b in at.button))
key = f"{'REJECTED' if args.reject else 'APPROVED'}-{run['approvalId']}"
at.button(key=key).click().run()
check(f"manager can {decision.lower()}", not at.exception, run["approvalId"])

# 3. the agent host resumes by itself (Kafka) and finishes
final = None
for _ in range(60):
    final = api.get_run(manager, wf)
    if final["status"] in ("COMPLETED", "PARTIALLY_COMPLETED", "REJECTED", "ESCALATED", "FAILED") and final["customerResponse"]:
        break
    time.sleep(2)
expected = "REJECTED" if args.reject else "COMPLETED"
check(f"workflow reaches {expected}", final["status"] == expected, final["status"])
if not args.reject:
    check("refund and replacement executed", bool(final["execution"]["refund_id"]) and bool(final["execution"]["replacement_order_id"]))

# 4. operator inspects the workflow in the explorer
at = session("operator", "Workflow explorer", workflow_id=wf).run()
check("explorer renders with the real data", not at.exception, str(at.exception)[:100] if at.exception else "")
check("trace, usage and RAG panels have data", {"Confidence", "Grounding", "Total tokens", "Estimated cost"} <= labels(at))
check("timeline, trace, RAG, Kafka, audit tabs", len(at.tabs) >= 5)

# 5. operator opens the admin panel
at = session("operator", "Admin").run()
check("admin panel renders", not at.exception, str(at.exception)[:100] if at.exception else "")
check("admin shows cost and RAG quality", {"Tokens", "Estimated cost", "Policy queries", "Cache hit rate"} <= labels(at))

# 6. policy assistant
at = session("customer", "Policy assistant").run()
at.button[0].click().run()
check("policy assistant answers with evidence", not at.exception and {"Confidence", "Grounding"} <= labels(at))

# 7. a customer cannot open someone else's workflow
at = session("customer", "Workflow explorer", workflow_id=wf, customer_id="CUST-2002").run()
check("another customer is refused", any("Cannot open" in e.value for e in at.error))

print("\nALL PASSED" if not failures else f"\nFAILED: {failures}")
print(f"workflow: {wf}")
sys.exit(1 if failures else 0)
