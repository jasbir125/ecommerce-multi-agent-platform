from pydantic import BaseModel


class ShipmentSnapshot(BaseModel):
    """Shipment facts from the carrier tool. verified=False means we could not check."""
    order_id: str
    verified: bool = True
    carrier: str | None = None
    tracking_no: str | None = None
    status: str | None = None
    delay_days: int = 0
    exception_type: str = "NONE"          # NONE | DELAYED | LOST
    last_event: str | None = None
