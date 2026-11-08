"""Authentication and authorization of the agent host API (service token + customer/role headers), with the real router."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router
from app.config import Settings

SERVICE_TOKEN = "local-demo-token"


class FakeRunner:
    """Just enough of RunManager for the routes under test."""
    def __init__(self):
        from app.guardrails.input_guardrails import RunRateLimiter
        self.ask_limiter = RunRateLimiter(100)
        self.deps = SimpleNamespace()

    async def get(self, wf):
        return {"workflowId": wf, "customerId": "CUST-1001", "status": "COMPLETED"}

    async def trace(self, wf):
        return {"workflowId": wf, "trace": []}

    async def start(self, **kw):
        return {"workflowId": "WF-1", "customer": kw["customer_id"]}

    async def resume(self, wf):
        return {"workflowId": wf}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    app.state.settings = Settings(mcp_service_token=SERVICE_TOKEN)
    app.state.runner = FakeRunner()
    return TestClient(app)


def who(customer=None, roles=None, token=SERVICE_TOKEN):
    h = {"X-Service-Token": token}
    if customer:
        h["X-Customer-Id"] = customer
    if roles:
        h["X-Roles"] = roles
    return h


def test_no_credentials_or_a_wrong_token_is_401(client):
    assert client.get("/api/agent/runs/WF-1").status_code == 401
    assert client.get("/api/agent/runs/WF-1", headers=who("CUST-1001", token="wrong")).status_code == 401


def test_a_customer_reads_their_own_run_but_not_someone_elses(client):
    assert client.get("/api/agent/runs/WF-1", headers=who("CUST-1001")).status_code == 200
    assert client.get("/api/agent/runs/WF-1", headers=who("CUST-2002")).status_code == 403
    assert client.get("/api/agent/runs/WF-1/trace", headers=who("CUST-2002")).status_code == 403


def test_an_operator_can_read_any_run(client):
    assert client.get("/api/agent/runs/WF-1", headers=who("OPS-1", "OPERATOR")).status_code == 200


def test_a_customer_cannot_start_a_run_for_someone_else(client):
    body = {"customerId": "CUST-2002", "orderId": "ORD-1", "message": "hi"}
    assert client.post("/api/agent/runs", json=body, headers=who("CUST-1001")).status_code == 403
    ok = client.post("/api/agent/runs", json={**body, "customerId": "CUST-1001"}, headers=who("CUST-1001"))
    assert ok.status_code == 200 and ok.json()["customer"] == "CUST-1001"


def test_admin_metrics_need_a_staff_role(client):
    assert client.get("/api/agent/admin/metrics", headers=who("CUST-1001")).status_code == 403
    # an operator passes the role check (the database is not available in this test, so it is a 503, not a 403)
    r = client.get("/api/agent/admin/metrics", headers=who("OPS-1", "OPERATOR"))
    assert r.status_code in (200, 503)


def test_oversized_bodies_and_security_headers():
    from app.main import app
    c = TestClient(app)                                                   # no lifespan: only the middleware runs
    big = c.post("/api/agent/runs", content=b"x", headers={"Content-Length": "1000000", "Content-Type": "application/json"})
    assert big.status_code == 413 and big.json()["detail"]["code"] == "PAYLOAD_TOO_LARGE"
    h = c.get("/health")
    assert h.headers["x-content-type-options"] == "nosniff" and h.headers["cache-control"] == "no-store"
