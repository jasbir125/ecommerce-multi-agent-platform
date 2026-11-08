from pydantic import BaseModel, Field


class StartRunRequest(BaseModel):
    customer_id: str = Field(alias="customerId")
    order_id: str = Field(alias="orderId")
    message: str
    workflow_id: str | None = Field(default=None, alias="workflowId")
    request_id: str | None = Field(default=None, alias="requestId")

    model_config = {"populate_by_name": True}
