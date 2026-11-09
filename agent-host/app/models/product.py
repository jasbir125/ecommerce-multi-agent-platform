from pydantic import BaseModel, Field

from app.models.money import Money


class ProductCandidate(BaseModel):
    product_id: str
    name: str
    price: Money
    currency: str | None = None
    available_qty: int


class ProductRecommendation(BaseModel):
    """product_id is None when nothing suitable is in stock. Candidates come from the catalog tool."""
    product_id: str | None = None
    name: str | None = None
    price: Money | None = None
    reason: str = ""
    max_price: Money | None = None
    candidates: list[ProductCandidate] = Field(default_factory=list)
