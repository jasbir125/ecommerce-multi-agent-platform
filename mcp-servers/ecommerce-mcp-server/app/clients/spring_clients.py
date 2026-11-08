"""One SpringClient per downstream domain service."""
from dataclasses import dataclass

from mcp_common.spring import SpringClient

from app.config import Settings


@dataclass
class Clients:
    orders: SpringClient
    shipments: SpringClient
    payments: SpringClient
    inventory: SpringClient
    approvals: SpringClient
    audit: SpringClient
    notifications: SpringClient

    @classmethod
    def from_settings(cls, s: Settings) -> "Clients":
        t = s.outbound_token
        return cls(
            orders=SpringClient(s.order_service_url, t),
            shipments=SpringClient(s.shipment_service_url, t),
            payments=SpringClient(s.payment_service_url, t),
            inventory=SpringClient(s.inventory_service_url, t),
            approvals=SpringClient(s.approval_service_url, t),
            audit=SpringClient(s.audit_service_url, t),
            notifications=SpringClient(s.notification_service_url, t),
        )
