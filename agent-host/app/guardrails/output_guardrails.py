"""Layer 2: validate what a model proposed, and what it tells the customer."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.resolution import ExecutionResult, ResolutionDecision

ALLOWED_ACTIONS = {"REFUND_AND_REPLACE", "REFUND_ONLY", "REPLACE_ONLY", "ESCALATE", "NO_ACTION"}


@dataclass
class DecisionContext:
    order_amount: float | None
    refund_eligible: bool
    policy_cited: bool
    candidate_prices: dict[str, float]      # product_id -> price, from the catalog tool
    original_price: float | None
    max_refund_amount: float = 500.0


def decision_violations(d: ResolutionDecision, ctx: DecisionContext) -> list[str]:
    v: list[str] = []
    if d.action not in ALLOWED_ACTIONS:
        v.append("INVALID_ACTION")
    refunds = d.action.startswith("REFUND")
    replaces = d.action.endswith("REPLACE") or d.action == "REPLACE_ONLY"
    if refunds:
        if not ctx.refund_eligible:
            v.append("POLICY_FORBIDS_REFUND")
        if d.refund_amount is None or d.refund_amount <= 0:
            v.append("REFUND_AMOUNT_MISSING")
        elif ctx.order_amount is not None and d.refund_amount > ctx.order_amount + 1e-9:
            v.append("REFUND_EXCEEDS_ORDER_AMOUNT")
        elif d.refund_amount > ctx.max_refund_amount:
            v.append("REFUND_EXCEEDS_LIMIT")
        if not d.approval_required:
            v.append("APPROVAL_REQUIRED")
        if not ctx.policy_cited:
            v.append("POLICY_NOT_CITED")
    if replaces:
        pid = d.replacement_product_id
        if not pid or pid not in ctx.candidate_prices:
            v.append("REPLACEMENT_NOT_IN_CATALOG_RESULTS")
        elif ctx.original_price is not None and ctx.candidate_prices[pid] > ctx.original_price + 1e-9:
            v.append("REPLACEMENT_COSTS_MORE")
    if d.action in ("REFUND_ONLY", "ESCALATE", "NO_ACTION") and d.replacement_product_id:
        v.append("UNEXPECTED_REPLACEMENT")
    if d.action != "NO_ACTION" and not d.evidence:
        v.append("EVIDENCE_REQUIRED")
    if d.risk_level not in {"LOW", "MEDIUM", "HIGH"}:
        v.append("INVALID_RISK_LEVEL")
    return v


_REFUND_CLAIM = re.compile(r"refund[^.]*\b(initiated|issued|processed|completed|sent|approved)\b|"
                           r"\b(initiated|issued|processed|completed)\b[^.]*refund", re.I)
_REPLACEMENT_CLAIM = re.compile(r"replacement[^.]*\b(created|placed|confirmed|on its way|shipped)\b", re.I)
_IDS = re.compile(r"\b(?:RF|ORD|RSV|APR|CC)-[A-Z0-9]{6,}\b")


_HUMAN_FOLLOW_UP = re.compile(r"support (agent|team)[^.]*\b(follow|contact|review)|manual review|will (contact|reach out)", re.I)


def response_violations(text: str, execution: ExecutionResult | None, order_id: str, status: str | None = None) -> list[str]:
    """The customer message may only state results a service actually returned, must state the ones that are verified,
    and must not contradict the outcome (for example promising human follow-up when everything completed)."""
    v: list[str] = []
    ex = execution or ExecutionResult()
    if ex.refund_status in ("INITIATED", "COMPLETED") and ex.refund_id and ex.refund_id not in text:
        v.append("MISSING_REFUND_ID")
    if ex.replacement_order_id and ex.replacement_order_id not in text:
        v.append("MISSING_REPLACEMENT_ID")
    if status == "COMPLETED" and _HUMAN_FOLLOW_UP.search(text):
        v.append("CONTRADICTS_OUTCOME")
    if _REFUND_CLAIM.search(text) and ex.refund_status not in ("INITIATED", "COMPLETED"):
        v.append("UNVERIFIED_REFUND_CLAIM")
    if _REPLACEMENT_CLAIM.search(text) and not ex.replacement_order_id:
        v.append("UNVERIFIED_REPLACEMENT_CLAIM")
    known = {order_id, ex.refund_id, ex.replacement_order_id, ex.reservation_id, ex.carrier_case_id} - {None}
    for found in _IDS.findall(text):
        if found not in known:
            v.append(f"FABRICATED_ID_{found}")
    return v
