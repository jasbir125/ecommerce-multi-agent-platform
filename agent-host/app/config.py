"""Runtime settings, read from environment variables (see the root .env.example)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- models -----------------------------------------------------------------
    model_provider: str = "anthropic"               # anthropic | openai
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5-5"
    openai_model: str = "gpt-4o-mini"
    # Optional per-agent overrides (see the recommended_model file); blank = provider default.
    model_supervisor: str | None = None
    model_order_agent: str | None = None
    model_shipment_agent: str | None = None
    model_policy_agent: str | None = None
    model_product_agent: str | None = None
    model_risk_agent: str | None = None
    model_decision_agent: str | None = None
    model_response_agent: str | None = None

    # --- platform connections ---------------------------------------------------
    mcp_service_token: str = "local-demo-token"
    ecommerce_mcp_url: str = "http://localhost:8090/mcp"
    policy_mcp_url: str = "http://localhost:8091/mcp"
    workflow_service_url: str = "http://localhost:8088"
    audit_service_url: str = "http://localhost:8087"
    database_url: str = "postgresql://ecommerce:ecommerce_local_pw@localhost:5432/ecommerce"
    redis_url: str = "redis://localhost:6379"
    kafka_bootstrap_servers: str = "localhost:9092"
    agent_kafka_enabled: bool = True
    checkpointer: str = "postgres"                  # postgres | memory
    otel_http_endpoint: str | None = None           # e.g. http://otel-collector:4318

    # --- guardrail limits -------------------------------------------------------
    max_message_length: int = 2000
    run_token_budget: int = 60000                   # per workflow run, all agents
    max_tool_calls_per_agent: int = 6
    min_policy_confidence: float = 0.5
    max_refund_amount: float = 500.0
    high_value_threshold: float = 100.0             # orders above this are MEDIUM risk
    runs_per_minute_per_customer: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
