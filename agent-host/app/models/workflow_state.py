"""The state that flows through the LangGraph workflow (checkpointed after every step)."""
import operator
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from app.models.order import OrderSnapshot
from app.models.policy import PolicyDecision
from app.models.product import ProductRecommendation
from app.models.resolution import ExecutionResult, ResolutionDecision
from app.models.risk import RiskAssessment
from app.models.shipment import ShipmentSnapshot

Status = Literal["STARTED", "INVESTIGATING", "WAITING_FOR_APPROVAL", "READY_FOR_EXECUTION", "COMPLETED",
                 "PARTIALLY_COMPLETED", "REJECTED", "ESCALATED", "FAILED"]


class WorkflowState(BaseModel):
    workflow_id: str
    request_id: str = ""
    correlation_id: str = ""            # id of the customer request that started this case; kept for every later step
    customer_id: str
    order_id: str
    customer_message: str
    intent: str = "DELAYED_ORDER_RECOVERY"

    order: OrderSnapshot | None = None
    shipment: ShipmentSnapshot | None = None
    policy: PolicyDecision | None = None
    product_recommendation: ProductRecommendation | None = None
    risk: RiskAssessment | None = None
    resolution: ResolutionDecision | None = None
    approval_id: str | None = None
    approval_status: str | None = None
    execution: ExecutionResult | None = None
    customer_response: str | None = None
    plan: list[str] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)

    status: Status = "STARTED"
    # These lists are appended to by several nodes (some in parallel), hence the reducer.
    errors: Annotated[list[str], operator.add] = Field(default_factory=list)
    evidence: Annotated[list[str], operator.add] = Field(default_factory=list)
    trace: Annotated[list[dict[str, Any]], operator.add] = Field(default_factory=list)
