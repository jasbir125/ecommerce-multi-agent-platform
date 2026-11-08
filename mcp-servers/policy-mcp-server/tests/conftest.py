import httpx
import pytest
import respx
from fastmcp import Client

from app.clients.policy_client import Deps
from app.config import Settings
from app.main import create_server


@pytest.fixture
def api(monkeypatch):
    with respx.mock(assert_all_called=False) as m:
        m.post("http://localhost:8087/api/audit/events").mock(return_value=httpx.Response(201, json={"id": 1}))
        yield m


@pytest.fixture
def client(api):
    s = Settings()
    return Client(create_server(s, Deps.build(s)))
