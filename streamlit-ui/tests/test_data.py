from datetime import datetime, timezone

from data import (approval_rows, audit_rows, candidate_rows, compensation_rows, decision_summary, event_rows, fmt_ms, fmt_usd,
                  grounding_summary, icon, is_terminal, label, parse_ts, timeline_rows, tool_call_rows, usage_rows, workflow_rows)
from tests.samples import RAG, RUN, TRACE, WORKFLOW, WF_TRACE


def test_time_and_money_formatting():
    assert fmt_ms(850) == "850 ms" and fmt_ms(2400) == "2.4 s" and fmt_ms(None) == "-"
    assert fmt_usd(0) == "$0" and fmt_usd(0.00084) == "$0.0008" and fmt_usd(12.5) == "$12.50"


def test_timestamps_without_a_zone_are_utc():
    assert parse_ts("2026-11-08T04:00:00").tzinfo == timezone.utc
    assert parse_ts("2026-11-08T04:00:00Z") == parse_ts("2026-11-08T04:00:00+00:00") and parse_ts(None) is None


def test_labels_and_status_icons():
    assert label("approval_wait") == "Approval wait" and label("something_new") == "Something New"
    assert icon("COMPLETED") == "✅" and icon("FAILED") == "❌" and icon("nope") == "•"
    assert is_terminal("COMPLETED") and not is_terminal("WAITING_FOR_APPROVAL") and not is_terminal(None)


def test_timeline_is_relative_to_the_first_step_and_parallel_steps_overlap():
    rows = {r["node"]: r for r in timeline_rows(TRACE)}
    assert rows["input_guard"]["start_ms"] == 0
    assert rows["order"]["start_ms"] < rows["shipment"]["end_ms"] and rows["shipment"]["start_ms"] < rows["order"]["end_ms"]   # ran together
    assert rows["decision"]["start_ms"] > rows["policy"]["end_ms"] - 1
    assert rows["approval_wait"]["status"] == "error" and rows["approval_wait"]["retries"] == 1 and rows["approval_wait"]["error"] == "boom"
    assert all(r["end_ms"] > r["start_ms"] for r in rows.values())


def test_timeline_survives_missing_timestamps():
    rows = timeline_rows([{"agent": "order", "latency_ms": 500}])
    assert rows[0]["end_ms"] == 500 and rows[0]["start_ms"] == 0


def test_tool_calls_are_listed_per_agent():
    calls = tool_call_rows(TRACE)
    assert ("Order agent", "get_order_status") in {(c["agent"], c["tool"]) for c in calls} and len(calls) == 5


def test_usage_rows_sort_by_tokens_and_name_rag_stages():
    rows = usage_rows(RUN["usage"])
    assert rows[0]["agent"] == "RAG generator" and rows[0]["total_tokens"] == 1020 and rows[1]["agent"] == "Decision agent"
    assert usage_rows(None) == []


def test_decision_summary_pulls_supervisor_reasoning_and_the_proposal():
    d = decision_summary(RUN, TRACE)
    assert d["action"] == "REFUND_AND_REPLACE" and d["supervisor_reasoning"] == "delayed order recovery" and d["refund_amount"] == 129.99


def test_candidates_show_scores_and_what_became_of_each_passage():
    rows = candidate_rows(RAG)
    assert [r["section"] for r in rows] == ["Delayed shipments", "Refund method and timing"]
    assert rows[0]["used_in_answer"] and rows[0]["evidence"].startswith("Orders delayed")
    assert not rows[1]["used_in_answer"] and rows[1]["in_evidence"]
    assert candidate_rows(None) == []


def test_grounding_summary_covers_every_outcome():
    assert grounding_summary(RAG)["label"] == "grounded" and grounding_summary(RAG)["dropped"] == ["injection:77"]
    assert grounding_summary({**RAG, "grounded": False})["label"] == "not grounded"
    assert grounding_summary({**RAG, "insufficientEvidence": True})["label"] == "insufficient evidence"
    assert grounding_summary(None)["label"] == "no evidence"


def test_event_rows_include_payloads_and_delivery_state():
    rows = event_rows(WF_TRACE["kafkaEvents"])
    assert rows[0]["delivered"] == "✅" and '"orderId": "ORD-7842"' in rows[0]["payload"] and rows[0]["correlation_id"] == "c1"
    assert event_rows([{"topic": "t", "event_id": "x"}])[0]["delivered"] == "⏳"


def test_audit_rows():
    assert audit_rows(WF_TRACE["auditEvents"])[0]["event"] == "WORKFLOW_STARTED"


def test_approval_countdown_and_expiry():
    now = datetime(2026, 11, 8, 10, 0, 0, tzinfo=timezone.utc)
    rows = approval_rows([{"expiresAt": "2026-11-08T10:25:30"}, {"expiresAt": "2026-11-08T09:59:00"}, {}], now)
    assert rows[0]["expires_in"] == "25m 30s" and rows[0]["seconds_left"] == 1530
    assert rows[1]["expires_in"] == "expired" and rows[2]["expires_in"] == "-"


def test_workflow_rows_and_compensation_rows():
    assert workflow_rows([WORKFLOW])[0]["status"].endswith("WAITING_FOR_APPROVAL")
    comp = compensation_rows([{**WORKFLOW, "status": "PARTIALLY_COMPLETED"}])
    assert [c["step"] for c in comp] == ["refund", "replacement_order", "release_inventory"]
    assert [c["compensation"] for c in comp] == ["", "", "↩️"]
    assert compensation_rows([{**WORKFLOW, "status": "COMPLETED", "state": {"execution": {"steps": [{"step": "refund", "ok": True}]}}}]) == []


def test_long_supervisor_reasoning_ends_cleanly_on_a_word():
    long = "The customer has reported a delayed order and requested a refund. " * 8
    out = decision_summary({"intent": "X"}, [{"agent": "supervisor", "detail": long}])["supervisor_reasoning"]
    assert out.endswith(" …") and not out[:-2].endswith("requeste") and len(out) <= 295
    assert decision_summary({"intent": "X"}, [{"agent": "supervisor", "detail": "short"}])["supervisor_reasoning"] == "short"
