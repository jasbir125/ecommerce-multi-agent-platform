import httpx
import pytest
import respx
from fastmcp import Client
from mcp_common.security import Identity

from app.config import Settings
from app.deps import Deps
from app.main import create_server

IDENT = Identity("CUST-1001", "WF-ABC12345", "corr-1", frozenset())


@pytest.fixture
def identity(monkeypatch):
    """Pretend an authenticated caller; tests change it via the returned holder."""
    holder = {"value": IDENT}
    fn = lambda: holder["value"]  # noqa: E731
    for mod in ("mcp_common.guardrails", "mcp_common.spring", "mcp_common.audit"):
        monkeypatch.setattr(f"{mod}.current_identity", fn)
    return holder


@pytest.fixture
def api():
    with respx.mock(assert_all_called=False) as m:
        m.post("http://localhost:8087/api/audit/events").mock(return_value=httpx.Response(201, json={"id": 1}))
        yield m


@pytest.fixture
def make_client(identity, api):
    def _make(**overrides):
        settings = Settings(**overrides)
        return Client(create_server(settings, Deps.build(settings)))
    return _make
