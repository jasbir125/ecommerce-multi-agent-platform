"""UI settings, read from environment variables."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    workflow_url: str = "http://localhost:8088"      # POST /api/workflows, trace, list
    approval_url: str = "http://localhost:8085"      # approvals
    agent_url: str = "http://localhost:8092"         # agent host: live runs, RAG, admin metrics
    audit_url: str = "http://localhost:8087"
    service_token: str = "local-demo-token"

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ
        return cls(
            workflow_url=e.get("STREAMLIT_API_BASE_URL", cls.workflow_url).rstrip("/"),
            approval_url=e.get("STREAMLIT_APPROVAL_API_BASE_URL", cls.approval_url).rstrip("/"),
            agent_url=e.get("AGENT_HOST_URL", cls.agent_url).rstrip("/"),
            audit_url=e.get("AUDIT_SERVICE_URL", cls.audit_url).rstrip("/"),
            service_token=e.get("STREAMLIT_SERVICE_TOKEN", e.get("MCP_SERVICE_TOKEN", cls.service_token)))
