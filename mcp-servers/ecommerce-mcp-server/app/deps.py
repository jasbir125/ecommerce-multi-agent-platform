"""Everything the tool modules need, built once at startup (and swappable in tests)."""
from dataclasses import dataclass

from mcp_common.audit import Auditor
from mcp_common.guardrails import WriteGuard, WriteLimits

from app.clients.spring_clients import Clients
from app.config import Settings


@dataclass
class Deps:
    clients: Clients
    guard: WriteGuard
    auditor: Auditor

    @classmethod
    def build(cls, settings: Settings, clients: Clients | None = None) -> "Deps":
        clients = clients or Clients.from_settings(settings)
        auditor = Auditor(clients.audit, "ecommerce-mcp-server")
        guard = WriteGuard(
            WriteLimits(settings.mcp_max_refund_amount, settings.mcp_max_quantity, settings.mcp_writes_per_minute),
            auditor, enabled=settings.mcp_write_tools_enabled)
        return cls(clients, guard, auditor)
