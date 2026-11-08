"""Runtime settings, read from environment variables (see .env.example)."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_provider: str = "anthropic"
    anthropic_model: str = "claude-sonnet-5-5"
    ecommerce_mcp_url: str = "http://localhost:8090/mcp"
    policy_mcp_url: str = "http://localhost:8091/mcp"
    workflow_service_url: str = "http://localhost:8088"
    kafka_bootstrap_servers: str = "localhost:9092"
    redis_url: str = "redis://localhost:6379"


settings = Settings()
