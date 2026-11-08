from pydantic import BaseModel, Field


class ProductCandidate(BaseModel):
    product_id: str
    name: str
    price: float
    available_qty: int


class ProductRecommendation(BaseModel):
    """product_id is None when nothing suitable is in stock. Candidates come from the catalog tool."""
    product_id: str | None = None
    name: str | None = None
    price: float | None = None
    reason: str = ""
    max_price: float | None = None
    candidates: list[ProductCandidate] = Field(default_factory=list)
