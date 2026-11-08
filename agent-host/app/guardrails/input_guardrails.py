"""Layer 1: check the customer request before any model sees it."""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

RULES = [
    ("BYPASS_APPROVAL", re.compile(r"(skip|bypass|without|no)\s+(the\s+)?(manager\s+)?approval|auto-?approve", re.I)),
    ("PROMPT_INJECTION", re.compile(r"ignore (all )?(the )?(previous|prior|above) instructions|system prompt|you are now|reveal your (prompt|instructions)", re.I)),
    ("DESTRUCTIVE_COMMAND", re.compile(r"drop\s+table|delete\s+from|rm\s+-rf|;\s*shutdown|curl\s+http", re.I)),
    ("OTHER_CUSTOMER_DATA", re.compile(r"(another|other|someone else'?s?)\s+customer'?s?\s+(order|data|account)", re.I)),
]
EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
CARD = re.compile(r"\b(?:\d[ -]?){13,16}\b")
PHONE = re.compile(r"(?<!\d)\+?\d[\d -]{8,}\d(?!\d)")
ORDER_ID = re.compile(r"^ORD-[A-Z0-9]{3,20}$")
CUSTOMER_ID = re.compile(r"^CUST-[A-Z0-9]{3,20}$")


def mask_pii(text: str) -> str:
    out = EMAIL.sub(r"\1***@\2", text)
    out = CARD.sub("[CARD-REDACTED]", out)
    return PHONE.sub("[PHONE-REDACTED]", out)


@dataclass
class InputCheck:
    ok: bool
    masked_message: str
    violations: list[str] = field(default_factory=list)


def check_request(message: str, customer_id: str, order_id: str, max_length: int = 2000) -> InputCheck:
    violations: list[str] = []
    if not message or not message.strip():
        violations.append("MISSING_MESSAGE")
    if len(message or "") > max_length:
        violations.append("MESSAGE_TOO_LONG")
    if not CUSTOMER_ID.match(customer_id or ""):
        violations.append("INVALID_CUSTOMER_ID")
    if not ORDER_ID.match(order_id or ""):
        violations.append("INVALID_ORDER_ID")
    for code, pattern in RULES:
        if pattern.search(message or ""):
            violations.append(code)
    return InputCheck(not violations, mask_pii(message or ""), violations)


class RunRateLimiter:
    """Max runs per customer per minute (in memory per process)."""

    def __init__(self, max_runs: int, window: float = 60.0):
        self.max_runs, self.window = max_runs, window
        self._calls: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, customer_id: str) -> bool:
        now, q = time.monotonic(), self._calls[customer_id]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.max_runs:
            return False
        q.append(now)
        return True
