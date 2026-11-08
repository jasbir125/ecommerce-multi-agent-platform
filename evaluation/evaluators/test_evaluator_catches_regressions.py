"""Mutation tests for the evaluation itself: if a guardrail is switched off, the matching golden scenario must fail.

A test suite that cannot fail proves nothing. Run: python -m pytest evaluation/evaluators -q  (from the repo root, agent-host venv)
"""
import asyncio
import json
from pathlib import Path

import pytest

from evaluation.evaluators.checks import evaluate
from evaluation.evaluators.component_runner import run_component

DATA = Path(__file__).resolve().parents[1] / "golden-datasets"


def case(scenario: str, case_id: str) -> dict:
    d = json.loads((DATA / f"{scenario}.json").read_text())
    return next(c for c in d["cases"] if c["id"] == case_id)


def failures(scenario: str, case_id: str) -> list[str]:
    c = case(scenario, case_id)
    return [n for n, ok, _ in evaluate(c, asyncio.run(run_component(c))) if not ok]


@pytest.mark.parametrize("scenario,case_id", [
    ("hallucinated-delivery-date", "hdd-promises-a-date"), ("hallucinated-refund-status", "hrs-claims-refund-on-escalation"),
    ("incorrect-policy-retrieval", "ipr-superseded-version"), ("invalid-model-output", "imo-unsafe-proposal"),
    ("refund-exceeds-order-amount", "rea-model-proposes-too-much"), ("low-confidence-decision", "lcd-weak-evidence")])
def test_unmodified_system_passes(scenario, case_id):
    assert failures(scenario, case_id) == []


def test_without_the_response_guardrail_a_promised_delivery_date_reaches_the_customer(monkeypatch):
    from app.agents import response_agent
    monkeypatch.setattr(response_agent, "response_violations", lambda *a, **k: [])
    f = failures("hallucinated-delivery-date", "hdd-promises-a-date")
    assert any("Friday" in n or "arrive" in n for n in f), f


def test_without_the_response_guardrail_a_false_refund_claim_reaches_the_customer(monkeypatch):
    from app.agents import response_agent
    monkeypatch.setattr(response_agent, "response_violations", lambda *a, **k: [])
    assert failures("hallucinated-refund-status", "hrs-claims-refund-on-escalation")


def test_without_the_decision_guardrail_an_oversized_refund_is_executed(monkeypatch):
    from app.agents import decision_agent
    monkeypatch.setattr(decision_agent, "decision_violations", lambda *a, **k: [])
    f = failures("refund-exceeds-order-amount", "rea-model-proposes-too-much")
    assert any("never exceeds" in n for n in f), f


def test_without_the_policy_guardrail_stale_evidence_authorises_a_refund(monkeypatch):
    from app.agents import decision_agent
    monkeypatch.setattr(decision_agent, "policy_violations", lambda *a, **k: [])
    f = failures("incorrect-policy-retrieval", "ipr-superseded-version")
    assert any("final status" in n or "forbidden" in n or "approval" in n for n in f), f


def test_without_the_relevance_check_an_offtopic_passage_grounds_a_refund(monkeypatch):
    from app.rag import pipeline
    monkeypatch.setattr(pipeline, "apply_ranking", lambda chunks, ranking: None)        # model relevance verdict ignored
    assert failures("incorrect-policy-retrieval", "ipr-wrong-topic")


def test_without_the_confidence_gate_weak_evidence_still_reaches_a_decision(monkeypatch):
    from app.agents import decision_agent
    monkeypatch.setattr(decision_agent, "blockers", lambda state, conf: [])
    assert failures("low-confidence-decision", "lcd-weak-evidence")
