"""Tool guardrails for write tools: checks that run before any state-changing call."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from fastmcp.exceptions import ToolError

from .audit import Auditor
from .security import RateLimiter, current_identity

WORKFLOW_ID = re.compile(r"^WF-[A-Z0-9]{6,20}$")
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,100}$")


@dataclass
class WriteLimits:
    max_refund_amount: Decimal = Decimal("500")
    max_quantity: int = 5
    writes_per_minute: int = 10


class WriteGuard:
    """Every write tool calls check() first. It enforces, in order: write tools enabled,
    caller identity, workflow id (matching the session's workflow), idempotency key,
    amount and quantity limits, and a per-customer rate limit."""

    def __init__(self, limits: WriteLimits, auditor: Auditor, enabled: bool = True):
        self.limits = limits
        self.auditor = auditor
        self.enabled = enabled
        self._rate = RateLimiter(limits.writes_per_minute)

    async def check(self, tool: str, workflow_id: str, idempotency_key: str | None = None,
                    amount: float | Decimal | None = None, quantity: int | None = None) -> None:
        if not self.enabled:
            raise ToolError("WRITE_TOOLS_DISABLED: state-changing tools are turned off on this server.")
        ident = current_identity()
        if not ident.customer_id:
            raise ToolError("IDENTITY_REQUIRED: no authenticated customer on this request.")
        if not WORKFLOW_ID.match(workflow_id or ""):
            raise ToolError("INVALID_WORKFLOW_ID: a valid workflow id (WF-...) is required.")
        if ident.workflow_id and ident.workflow_id != workflow_id:
            await self.auditor.record("GUARDRAIL_WORKFLOW_MISMATCH", ident.workflow_id,
                                      {"tool": tool, "attempted": workflow_id})
            raise ToolError("WORKFLOW_MISMATCH: this session may only act on its own workflow.")
        if idempotency_key is not None and not IDEMPOTENCY_KEY.match(idempotency_key):
            raise ToolError("IDEMPOTENCY_KEY_REQUIRED: provide a stable key of 8-100 safe characters.")
        if amount is not None and not (Decimal("0") < Decimal(str(amount)) <= self.limits.max_refund_amount):
            raise ToolError(f"AMOUNT_OUT_OF_RANGE: amount must be > 0 and <= {self.limits.max_refund_amount}.")
        if quantity is not None and not (1 <= quantity <= self.limits.max_quantity):
            raise ToolError(f"QUANTITY_OUT_OF_RANGE: quantity must be 1-{self.limits.max_quantity}.")
        if not self._rate.allow(ident.customer_id):
            raise ToolError("RATE_LIMITED: too many write calls; retry later.")
