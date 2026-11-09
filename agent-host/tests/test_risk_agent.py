"""Risk Agent: deterministic rules with named thresholds."""
from decimal import Decimal

import pytest

from app.agents import risk_agent
from app.agents.risk_agent import RiskThresholds, assess, level_for
from app.models.policy import PolicyDecision
from tests.agent_fakes import make_deps, make_state, order, shipment


async def run(**state):
    deps, _ = make_deps(high_value_threshold="100", max_refund_amount="500")
    return await risk_agent.run(make_state(**state), deps, {})


async def test_happy_path_through_run():
    out = await run(order=order(50), shipment=shipment())
    assert out["risk"].level == "LOW" and out["evidence"][0].startswith("risk LOW") and out["errors"] == []


async def test_missing_facts_are_high_risk():
    out = await run(order=order(50))
    assert out["risk"].level == "HIGH" and out["risk"].factors == ["FACTS_MISSING"]


@pytest.mark.parametrize("score,level", [(0.29, "LOW"), (0.30, "MEDIUM"), (0.59, "MEDIUM"), (0.60, "HIGH")])
def test_level_boundaries(score, level):
    assert level_for(score) == level


def test_amount_thresholds_are_inclusive_and_use_decimal():
    thresholds = RiskThresholds(high_value=Decimal("100"), max_refund=Decimal("500"))
    assert risk_agent.is_high_value(order(100.00), thresholds) and not risk_agent.is_high_value(order(99.99), thresholds)
    assert risk_agent.is_above_refund_limit(order(500), thresholds) and not risk_agent.is_above_refund_limit(order(499.99), thresholds)


def test_each_rule_adds_its_factor():
    result = assess(order(900, payment="PENDING"), shipment("NONE"), PolicyDecision(injection_suspected=True), 100, 500)
    assert {"ORDER_ABOVE_REFUND_LIMIT", "NO_CARRIER_EXCEPTION_CONFIRMED", "PAYMENT_NOT_CAPTURED", "POLICY_TEXT_FLAGGED"} <= set(result.factors)
    assert result.level == "HIGH" and result.score == 1.0          # clamped


def test_unverified_shipment_is_a_factor_not_a_confirmed_exception():
    result = assess(order(50), shipment(verified=False), None, 100, 500)
    assert "SHIPMENT_UNVERIFIED" in result.factors and not any(f.startswith("CARRIER_CONFIRMS") for f in result.factors)


def test_confirmed_carrier_exception_lowers_the_score():
    assert assess(order(50), shipment("DELAYED"), None, 100, 500).factors == ["CARRIER_CONFIRMS_DELAYED"]
