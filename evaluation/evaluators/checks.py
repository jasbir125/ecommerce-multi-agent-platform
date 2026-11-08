"""Observed behaviour of one test case, and the comparison against what the dataset expects."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Observed:
    intent: str | None = None
    tools: set[str] = field(default_factory=set)
    approval_required: bool | None = None
    final_status: str | None = None
    audit_events: set[str] = field(default_factory=set)
    kafka_topics: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    message: str = ""
    rag: dict[str, Any] = field(default_factory=dict)        # confidence, grounded, citations[{policyKey, version}]
    state: dict[str, Any] = field(default_factory=dict)      # refund_rows, approval_rows, agent_runs, refund_amount_max, ...
    access: list[dict[str, Any]] = field(default_factory=list)
    step_checks: list[tuple[str, bool, str]] = field(default_factory=list)   # api / mcp / setup checks made by the runner
    notes: list[str] = field(default_factory=list)


Check = tuple[str, bool, str]


def evaluate(case: dict[str, Any], o: Observed) -> list[Check]:
    """One check per expectation in the dataset. Only expectations the case states are checked."""
    out: list[Check] = list(o.step_checks)

    def chk(name: str, ok: bool, detail: str = "") -> None:
        out.append((name, bool(ok), detail))

    if case.get("expected_intent") is not None:
        chk("intent", o.intent == case["expected_intent"], f"expected {case['expected_intent']}, got {o.intent}")
    tools = case.get("expected_tools") or {}
    missing = [t for t in tools.get("must_call", []) if t not in o.tools]
    chk("tools: required calls made", not missing, f"missing {missing}; made {sorted(o.tools)}" if missing else "")
    forbidden = [t for t in tools.get("must_not_call", []) if t in o.tools]
    chk("tools: forbidden calls not made", not forbidden, f"made {forbidden}" if forbidden else "")
    if case.get("expected_approval_required") is not None:
        chk("approval requirement", o.approval_required == case["expected_approval_required"],
            f"expected {case['expected_approval_required']}, got {o.approval_required}")
    if case.get("expected_final_status") is not None:
        chk("final status", o.final_status == case["expected_final_status"], f"expected {case['expected_final_status']}, got {o.final_status}")

    g = case.get("expected_guardrail_behavior") or {}
    msg = o.message or ""
    low = msg.lower()
    for ev in g.get("audit_events_present", []):
        chk(f"audit event present: {ev}", ev in o.audit_events, f"seen {sorted(o.audit_events)}" if ev not in o.audit_events else "")
    for ev in g.get("audit_events_absent", []):
        chk(f"audit event absent: {ev}", ev not in o.audit_events)
    for t in g.get("kafka_topics_present", []):
        chk(f"kafka topic published: {t}", t in o.kafka_topics, f"seen {sorted(o.kafka_topics)}" if t not in o.kafka_topics else "")
    for s in g.get("message_includes", []):
        chk(f"message includes '{s}'", s.lower() in low, msg[:120])
    if g.get("message_includes_any"):
        chk(f"message includes one of {g['message_includes_any']}", any(s.lower() in low for s in g["message_includes_any"]), msg[:120])
    for s in g.get("message_excludes", []):
        chk(f"message excludes '{s}'", s.lower() not in low, msg[:120] if s.lower() in low else "")
    for rx in g.get("message_matches", []):
        chk(f"message matches /{rx}/", re.search(rx, msg, re.I) is not None, msg[:120])
    for rx in g.get("message_must_not_match", []):
        chk(f"message must not match /{rx}/", re.search(rx, msg, re.I) is None, msg[:120] if re.search(rx, msg, re.I) else "")
    for e in g.get("errors_include", []):
        chk(f"error recorded: {e}", any(e in x for x in o.errors), f"errors: {[x[:60] for x in o.errors][:6]}" if not any(e in x for x in o.errors) else "")

    r = case.get("expected_rag_confidence")
    if r:
        conf = float(o.rag.get("confidence", 0))
        chk(f"rag confidence in [{r.get('min', 0)}, {r.get('max', 1)}]", r.get("min", 0) <= conf <= r.get("max", 1), f"got {conf:.2f}")
        if "grounded" in r:
            chk(f"rag grounded = {r['grounded']}", bool(o.rag.get("grounded")) == r["grounded"], f"got {o.rag.get('grounded')}")
    for cit in case.get("expected_citations", []):
        found = any(c["policyKey"] == cit["policyKey"] and (cit.get("version") is None or c.get("version") == cit["version"]) for c in o.rag.get("citations", []))
        chk(f"cites {cit['policyKey']} v{cit.get('version', '*')}", found, f"cited {o.rag.get('citations')}" if not found else "")

    st = case.get("expected_state") or {}
    for key, want in st.items():
        got = o.state.get(key)
        if key == "refund_amount_max":
            chk("refund never exceeds the order amount", got is None or got <= want + 1e-9, f"refunded {got}, order {want}")
        elif key == "replacement_product_not":
            chk(f"replacement is not {want}", o.state.get("replacement_product") != want, f"got {o.state.get('replacement_product')}")
        else:
            chk(f"state {key} = {want}", got == want, f"got {got}")
    for den in case.get("access_denials", []):
        hit = next((a for a in o.access if a["actor"] == den["actor"] and a["resource"] == den["resource"]), None)
        chk(f"{den['actor']} denied on {den['resource']} ({den['status']})", hit is not None and hit["status"] == den["status"], f"got {hit}")
    return out
