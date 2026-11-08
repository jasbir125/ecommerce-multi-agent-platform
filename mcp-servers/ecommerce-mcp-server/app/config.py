"""Settings, read from environment variables (see the root .env.example)."""
from decimal import Decimal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Inbound: token the agent host must present. Outbound: token Spring services expect.
    mcp_service_token: str = "local-demo-token"
    platform_service_token: str | None = None

    order_service_url: str = "http://localhost:8081"
    shipment_service_url: str = "http://localhost:8082"
    payment_service_url: str = "http://localhost:8083"
    inventory_service_url: str = "http://localhost:8084"
    approval_service_url: str = "http://localhost:8085"
    audit_service_url: str = "http://localhost:8087"
    notification_service_url: str = "http://localhost:8089"

    ecommerce_mcp_port: int = Field(8090, alias="ECOMMERCE_MCP_PORT")
    mcp_write_tools_enabled: bool = True
    mcp_max_refund_amount: Decimal = Decimal("500")
    mcp_max_quantity: int = 5
    mcp_writes_per_minute: int = 30

    @property
    def outbound_token(self) -> str:
        return self.platform_service_token or self.mcp_service_token


def get_settings() -> Settings:
    return Settings()
