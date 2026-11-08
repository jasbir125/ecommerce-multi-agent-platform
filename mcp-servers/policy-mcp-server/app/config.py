"""Settings, read from environment variables (see the root .env.example)."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mcp_service_token: str = "local-demo-token"
    platform_service_token: str | None = None
    policy_service_url: str = "http://localhost:8086"
    audit_service_url: str = "http://localhost:8087"
    policy_mcp_port: int = Field(8091, alias="POLICY_MCP_PORT")

    @property
    def outbound_token(self) -> str:
        return self.platform_service_token or self.mcp_service_token


def get_settings() -> Settings:
    return Settings()
