"""Risk Agent: rules decide (no model), because risk limits must be predictable and auditable.

It assesses fraud and business risk from verified facts and returns a level with the reasons. It executes nothing.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

from app.agents.base import AgentRun, AgentUpdate
from app.models.order import OrderSnapshot
from app.models.policy import PolicyDecision
from app.models.risk import RiskAssessment
from app.models.shipment import ShipmentSnapshot

log = logging.getLogger("agent.risk")

# ---------------------------------------------------------------------------------------------- constants
AGENT_NAME = "risk"
RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]
BASE_SCORE = 0.1
HIGH_LEVEL_AT = 0.6
MEDIUM_LEVEL_AT = 0.3
FACTS_MISSING_SCORE = 0.9
ABOVE_REFUND_LIMIT_POINTS = 0.5
HIGH_VALUE_POINTS = 0.25
SHIPMENT_UNVERIFIED_POINTS = 0.5
NO_CARRIER_EXCEPTION_POINTS = 0.4
CARRIER_CONFIRMED_POINTS = -0.05
PAYMENT_NOT_CAPTURED_POINTS = 0.4
POLICY_FLAGGED_POINTS = 0.2
CAPTURED = "CAPTURED"
NO_EXCEPTION = "NONE"


# -------------------------------------------------------------------------------------------------- models
class RiskThresholds(BaseModel):
    high_value: Decimal
    max_refund: Decimal


class RiskInput(BaseModel):
    order: OrderSnapshot
    shipment: ShipmentSnapshot
    policy: PolicyDecision | None = None
    thresholds: RiskThresholds


class RiskFactor(BaseModel):
    code: str
    points: float


class RiskResult(AgentUpdate):
    risk: RiskAssessment | None = None


# ------------------------------------------------------------------------------------------------- run()
async def run(state, deps, config) -> dict[str, Any]:
    timer = AgentRun(AGENT_NAME, state)
    thresholds = prepare_context(deps.settings)
    request = validate_input(state, thresholds)
    if request is None:
        return timer.finish(handle_error()).to_update()
    factors = apply_business_rules(request)
    assessment = select_decision(factors)
    return timer.finish(build_result(assessment)).to_update()


# ---------------------------------------------------------------------------------------- validate_input()
def validate_input(state, thresholds: RiskThresholds) -> RiskInput | None:
    """None when the order or shipment facts are missing: that case is itself high risk."""
    if state.order is None or state.shipment is None:
        return None
    return RiskInput(order=state.order, shipment=state.shipment, policy=state.policy, thresholds=thresholds)


# --------------------------------------------------------------------------------------- prepare_context()
def prepare_context(settings) -> RiskThresholds:
    return RiskThresholds(high_value=settings.high_value_threshold, max_refund=settings.max_refund_amount)


# ------------------------------------------------------------------------------- invoke_tools_or_agent()
# (This agent uses no model and no tools; everything it needs is already verified workflow state.)


# ------------------------------------------------------------------------------- normalize_tool_results()
# (Nothing to normalize: the inputs are typed snapshots written by the order, shipment and policy agents.)


# ------------------------------------------------------------------------------------ apply_business_rules()
def apply_business_rules(request: RiskInput) -> list[RiskFactor]:
    """Every rule that fired, in a fixed order, each with its points."""
    return [*order_amount_factors(request), *shipment_factors(request.shipment), *payment_factors(request.order),
            *policy_factors(request.policy)]


def is_above_refund_limit(order: OrderSnapshot, thresholds: RiskThresholds) -> bool:
    return order.total_amount >= thresholds.max_refund


def is_high_value(order: OrderSnapshot, thresholds: RiskThresholds) -> bool:
    return order.total_amount >= thresholds.high_value


def has_carrier_confirmed_exception(shipment: ShipmentSnapshot) -> bool:
    return shipment.verified and shipment.exception_type != NO_EXCEPTION


def has_no_carrier_exception(shipment: ShipmentSnapshot) -> bool:
    return shipment.verified and shipment.exception_type == NO_EXCEPTION


def order_amount_factors(request: RiskInput) -> list[RiskFactor]:
    if is_above_refund_limit(request.order, request.thresholds):
        return [RiskFactor(code="ORDER_ABOVE_REFUND_LIMIT", points=ABOVE_REFUND_LIMIT_POINTS)]
    if is_high_value(request.order, request.thresholds):
        return [RiskFactor(code="HIGH_VALUE_ORDER", points=HIGH_VALUE_POINTS)]
    return []


def shipment_factors(shipment: ShipmentSnapshot) -> list[RiskFactor]:
    if not shipment.verified:
        return [RiskFactor(code="SHIPMENT_UNVERIFIED", points=SHIPMENT_UNVERIFIED_POINTS)]
    if has_no_carrier_exception(shipment):
        return [RiskFactor(code="NO_CARRIER_EXCEPTION_CONFIRMED", points=NO_CARRIER_EXCEPTION_POINTS)]
    return [RiskFactor(code=f"CARRIER_CONFIRMS_{shipment.exception_type}", points=CARRIER_CONFIRMED_POINTS)]


def payment_factors(order: OrderSnapshot) -> list[RiskFactor]:
    if order.payment_status == CAPTURED:
        return []
    return [RiskFactor(code="PAYMENT_NOT_CAPTURED", points=PAYMENT_NOT_CAPTURED_POINTS)]


def policy_factors(policy: PolicyDecision | None) -> list[RiskFactor]:
    if policy is not None and policy.injection_suspected:
        return [RiskFactor(code="POLICY_TEXT_FLAGGED", points=POLICY_FLAGGED_POINTS)]
    return []


# ----------------------------------------------------------------------------------------- select_decision()
def select_decision(factors: list[RiskFactor]) -> RiskAssessment:
    score = clamp_score(BASE_SCORE + sum(f.points for f in factors))
    return RiskAssessment(level=level_for(score), score=score, factors=[f.code for f in factors])


def clamp_score(raw: float) -> float:
    return round(max(0.0, min(raw, 1.0)), 2)


def level_for(score: float) -> RiskLevel:
    if score >= HIGH_LEVEL_AT:
        return "HIGH"
    if score >= MEDIUM_LEVEL_AT:
        return "MEDIUM"
    return "LOW"


def assess(order, shipment, policy, high_value, max_refund) -> RiskAssessment:
    """Assess risk from plain values (kept for callers and tests that do not have a workflow state)."""
    if order is None or shipment is None:
        return facts_missing_assessment()
    thresholds = RiskThresholds(high_value=high_value, max_refund=max_refund)
    return select_decision(apply_business_rules(RiskInput(order=order, shipment=shipment, policy=policy, thresholds=thresholds)))


# ----------------------------------------------------------------------------------------- build_evidence()
def build_evidence(assessment: RiskAssessment) -> list[str]:
    return [f"risk {assessment.level} ({', '.join(assessment.factors)})"]


# --------------------------------------------------------------------------------------------- build_result()
def build_result(assessment: RiskAssessment) -> RiskResult:
    return RiskResult(risk=assessment, evidence=build_evidence(assessment))


# -------------------------------------------------------------------------------------------- handle_error()
def facts_missing_assessment() -> RiskAssessment:
    return RiskAssessment(level="HIGH", score=FACTS_MISSING_SCORE, factors=["FACTS_MISSING"])


def handle_error() -> RiskResult:
    """Missing facts are high risk: the decision agent will escalate."""
    return build_result(facts_missing_assessment())
