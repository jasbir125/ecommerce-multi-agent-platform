"""Money inside the agent host: always Decimal, rounded to cents, and a plain JSON number on the way out.

Rules used everywhere an amount is compared:
- an amount is converted with `to_money` (through str(), so a float such as 79.99 brings no binary noise) and rounded half-up to cents;
- two amounts are compared with `is_within_budget`, whose tolerance is explicit (`PRICE_TOLERANCE`);
- amounts in different currencies are never compared (`same_currency`).
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer

CENT = Decimal("0.01")
PRICE_TOLERANCE = Decimal("0.00")        # amounts are rounded to cents first, so "not more than" is exact
DEFAULT_CURRENCY = "USD"


def to_money(value: Any) -> Decimal:
    """Parse an amount from a number or string and round it to cents. Raises ValueError for anything else."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"not an amount: {value!r}")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation as e:
        raise ValueError(f"not an amount: {value!r}") from e
    if not amount.is_finite():
        raise ValueError(f"not an amount: {value!r}")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def money_as_number(amount: Decimal) -> float:
    """The one place an amount becomes a float: the JSON boundary to services and the UI."""
    return float(amount)


def is_within_budget(amount: Decimal, budget: Decimal, tolerance: Decimal = PRICE_TOLERANCE) -> bool:
    return amount <= budget + tolerance


def same_currency(first: str | None, second: str | None) -> bool:
    """Unknown (missing) currencies are treated as the default currency, as the order tools do."""
    return (first or DEFAULT_CURRENCY).upper() == (second or DEFAULT_CURRENCY).upper()


# A pydantic field type: parsed and rounded on input, a JSON number on output (python-mode dumps keep the Decimal).
Money = Annotated[Decimal, BeforeValidator(to_money),
                  PlainSerializer(money_as_number, return_type=float, when_used="json")]
