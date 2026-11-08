"""Risk Agent: rules decide (no model), because risk limits must be predictable and auditable."""
from app.models.risk import RiskAssessment


def assess(order, shipment, policy, high_value: float, max_refund: float) -> RiskAssessment:
    factors, score = [], 0.1
    if order is None or shipment is None:
        return RiskAssessment(level="HIGH", score=0.9, factors=["FACTS_MISSING"])
    if order.total_amount >= max_refund:
        factors.append("ORDER_ABOVE_REFUND_LIMIT")
        score += 0.5
    elif order.total_amount >= high_value:
        factors.append("HIGH_VALUE_ORDER")
        score += 0.25
    if not shipment.verified:
        factors.append("SHIPMENT_UNVERIFIED")
        score += 0.5
    elif shipment.exception_type == "NONE":
        factors.append("NO_CARRIER_EXCEPTION_CONFIRMED")
        score += 0.4
    else:
        factors.append(f"CARRIER_CONFIRMS_{shipment.exception_type}")
        score -= 0.05
    if order.payment_status != "CAPTURED":
        factors.append("PAYMENT_NOT_CAPTURED")
        score += 0.4
    if policy is not None and policy.injection_suspected:
        factors.append("POLICY_TEXT_FLAGGED")
        score += 0.2
    score = round(max(0.0, min(score, 1.0)), 2)
    level = "HIGH" if score >= 0.6 else "MEDIUM" if score >= 0.3 else "LOW"
    return RiskAssessment(level=level, score=score, factors=factors)


async def run(state, deps, config) -> dict:
    s = deps.settings
    r = assess(state.order, state.shipment, state.policy, s.high_value_threshold, s.max_refund_amount)
    return {"risk": r, "evidence": [f"risk {r.level} ({', '.join(r.factors)})"]}
