"""Layer 2: validate what a model proposed, and what it tells the customer."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.models.money import is_below, is_within_budget, to_money
from app.models.resolution import ExecutionResult, ResolutionDecision

ALLOWED_ACTIONS = {"REFUND_AND_REPLACE", "REFUND_ONLY", "REPLACE_ONLY", "ESCALATE", "NO_ACTION"}


@dataclass
class DecisionContext:
    order_amount: Decimal | None
    refund_eligible: bool
    policy_cited: bool
    candidate_prices: dict[str, Decimal]    # product_id -> price, from the catalog tool
    original_price: Decimal | None
    max_refund_amount: Decimal = Decimal("500.00")

    def __post_init__(self) -> None:
        """Amounts may be given as numbers; they are compared as Decimal cents."""
        self.order_amount = None if self.order_amount is None else to_money(self.order_amount)
        self.original_price = None if self.original_price is None else to_money(self.original_price)
        self.candidate_prices = {pid: to_money(price) for pid, price in self.candidate_prices.items()}
        self.max_refund_amount = to_money(self.max_refund_amount)


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
        elif ctx.order_amount is not None and not is_within_budget(d.refund_amount, ctx.order_amount):
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
        elif ctx.original_price is not None and not is_below(ctx.candidate_prices[pid], ctx.original_price):
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


# We never know a delivery date: carriers lose parcels. The customer text must not promise one.
_DELIVERY_PROMISE = re.compile(
    r"\b(will|should|is expected to|are expected to|going to|shall)\b[^.]{0,40}\b(arrive|be delivered|deliver|be shipped|ship|dispatch|reach you)\b"
    r"|\b(delivery|arrival|dispatch|shipping) (date|time|estimate|window|eta)\b|\beta\b"
    r"|\b(arrive|arrives|delivered|delivery|shipped|dispatched)\b[^.]{0,30}\b(by|on|within|before|in)\s+(\d|tomorrow|today|monday|tuesday|wednesday|thursday|friday|saturday|sunday|next|the end|a few|two|three|four|five|seven)",
    re.I)
_APPROVAL_CLAIM = re.compile(r"\b(approved|approval (was|has been|is) (granted|given|complete|completed))\b", re.I)
_HUMAN_FOLLOW_UP = re.compile(r"support (agent|team)[^.]*\b(follow|contact|review)|manual review|will (contact|reach out)", re.I)


def response_violations(text: str, execution: ExecutionResult | None, order_id: str, status: str | None = None,
                        approval_status: str | None = None) -> list[str]:
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
    if _DELIVERY_PROMISE.search(text):
        v.append("UNSUPPORTED_DELIVERY_PROMISE")
    if _APPROVAL_CLAIM.search(text) and approval_status != "APPROVED":
        v.append("UNVERIFIED_APPROVAL_CLAIM")
    if _REFUND_CLAIM.search(text) and ex.refund_status not in ("INITIATED", "COMPLETED"):
        v.append("UNVERIFIED_REFUND_CLAIM")
    if _REPLACEMENT_CLAIM.search(text) and not ex.replacement_order_id:
        v.append("UNVERIFIED_REPLACEMENT_CLAIM")
    known = {order_id, ex.refund_id, ex.replacement_order_id, ex.reservation_id, ex.carrier_case_id} - {None}
    for found in _IDS.findall(text):
        if found not in known:
            v.append(f"FABRICATED_ID_{found}")
    return v
