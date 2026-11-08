"""Clients for the Spring policy-service (and audit-service)."""
from dataclasses import dataclass

from mcp_common.audit import Auditor
from mcp_common.spring import SpringClient

from app.config import Settings


@dataclass
class Deps:
    policy: SpringClient
    auditor: Auditor

    @classmethod
    def build(cls, s: Settings, policy: SpringClient | None = None) -> "Deps":
        audit = SpringClient(s.audit_service_url, s.outbound_token)
        return cls(policy or SpringClient(s.policy_service_url, s.outbound_token),
                   Auditor(audit, "policy-mcp-server"))
