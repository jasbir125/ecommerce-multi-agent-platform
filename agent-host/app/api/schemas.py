from pydantic import BaseModel, Field


class AskPolicyRequest(BaseModel):
    question: str
    region: str | None = None
    policy_type: str | None = Field(default=None, alias="policyType")
    product_category: str | None = Field(default=None, alias="productCategory")
    customer_segment: str | None = Field(default=None, alias="customerSegment")
    version: str | None = None
    as_of_date: str | None = Field(default=None, alias="asOfDate")

    model_config = {"populate_by_name": True}


class StartRunRequest(BaseModel):
    customer_id: str = Field(alias="customerId")
    order_id: str = Field(alias="orderId")
    message: str
    workflow_id: str | None = Field(default=None, alias="workflowId")
    request_id: str | None = Field(default=None, alias="requestId")

    model_config = {"populate_by_name": True}
