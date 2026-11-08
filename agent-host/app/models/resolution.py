from typing import Literal

from pydantic import BaseModel, Field

Action = Literal["REFUND_AND_REPLACE", "REFUND_ONLY", "REPLACE_ONLY", "ESCALATE", "NO_ACTION"]


class ResolutionDecision(BaseModel):
    """A proposal. Nothing happens until approval and deterministic execution."""
    action: Action
    refund_amount: float | None = None
    replacement_product_id: str | None = None
    approval_required: bool = True
    risk_level: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)


class StepResult(BaseModel):
    step: str
    ok: bool
    detail: str = ""
    data: dict = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    """What actually happened, taken from service responses."""
    steps: list[StepResult] = Field(default_factory=list)
    refund_id: str | None = None
    refund_status: str | None = None
    reservation_id: str | None = None
    replacement_order_id: str | None = None
    carrier_case_id: str | None = None
    compensated: bool = False
