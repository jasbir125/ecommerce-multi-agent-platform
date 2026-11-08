from pydantic import BaseModel


class OrderSnapshot(BaseModel):
    """Order facts exactly as returned by the order tools (never written by a model)."""
    order_id: str
    status: str
    payment_status: str
    total_amount: float
    currency: str = "USD"
    product_id: str | None = None
    product_name: str | None = None
    quantity: int | None = None
