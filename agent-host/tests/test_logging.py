"""Correlated logging: masking, the access-log middleware, and the log line format."""
import json
import logging

import httpx
import pytest
from fastapi import Depends, FastAPI, Request

from app.observability import logctx
from app.observability.http_log import RequestLogMiddleware


def test_clean_masks_secrets_and_personal_data_but_keeps_business_values():
    out = logctx.clean({"orderId": "ORD-8801", "amount": 129.99, "password": "hunter2", "accessToken": "abc",
                        "note": "mail alice@example.com or +1 415 555 2671 key sk-proj-abcdefghijklmnop1234"})
    assert "ORD-8801" in out and "129.99" in out
    for secret in ("hunter2", "alice@example.com", "555 2671", "sk-proj"):
        assert secret not in out
    assert '"password": "***"' in out


def test_clean_truncates_and_does_not_touch_dates_or_ids():
    assert logctx.clean("ORD-8801 on 2026-11-08T10:00:00Z") == "ORD-8801 on 2026-11-08T10:00:00Z"
    cut = logctx.clean("x" * 100, 20)
    assert cut.startswith("x" * 20) and "(+80 chars)" in cut


def test_bind_scope_and_request_info():
    info: dict = {}
    t = logctx.request_info.set(info)
    try:
        with logctx.scope(correlation_id="c-1", workflow_id="WF-1"):
            assert logctx.correlation_id.get() == "c-1"
        assert logctx.correlation_id.get() is None            # restored
        logctx.bind(customer_id="CUST-9")
        assert info["customer_id"] == "CUST-9"                # visible to the middleware even from a child task
    finally:
        logctx.request_info.reset(t)


@pytest.fixture
def app():
    api = FastAPI()

    async def who(request: Request):
        logctx.bind(customer_id="CUST-1001")

    @api.post("/api/agent/runs", dependencies=[Depends(who)])
    async def runs(body: dict):
        logging.getLogger("agent.test").info("inside the handler")
        return {"workflowId": "WF-ABC123", "status": "STARTED", "accessToken": "secret-token"}

    @api.get("/health")
    async def health():
        return {"ok": True}

    api.add_middleware(RequestLogMiddleware, service="test")
    return api


async def call(app, **kw):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.post("/api/agent/runs", json={"orderId": "ORD-1", "password": "pw"}, **kw)


async def test_middleware_logs_in_and_out_with_correlation_bodies_and_caller(app, caplog):
    logctx_filter = logctx.ContextFilter()
    caplog.handler.addFilter(logctx_filter)
    with caplog.at_level(logging.INFO):
        r = await call(app, headers={"X-Correlation-Id": "corr-42"})
    assert r.headers["x-correlation-id"] == "corr-42"
    lines = [rec.getMessage() for rec in caplog.records if rec.name == "http.access"]
    assert lines[0].startswith("-> POST /api/agent/runs") and "corr=corr-42" in lines[0]
    out = lines[1]
    assert out.startswith("<- POST /api/agent/runs 200") and "caller=CUST-1001" in out and "wf=WF-ABC123" in out
    assert "ORD-1" in out and "STARTED" in out
    assert "secret-token" not in out and '"pw"' not in out
    inner = next(rec for rec in caplog.records if rec.name == "agent.test")
    assert inner.corr == "corr-42"                            # code inside the handler logs the same id


async def test_middleware_creates_an_id_and_skips_health(app, caplog):
    with caplog.at_level(logging.INFO):
        r = await call(app)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await c.get("/health")
    assert len(r.headers["x-correlation-id"]) >= 32
    assert not any("/health" in rec.getMessage() for rec in caplog.records if rec.name == "http.access")


def test_text_and_json_formats_show_the_ids(monkeypatch, capsys):
    for fmt in ("text", "json"):
        monkeypatch.setenv("LOG_FORMAT", fmt)
        logctx.setup_logging("agent-host")
        with logctx.scope(correlation_id="corr-7", workflow_id="WF-7", agent="policy"):
            logging.getLogger("agent.x").info("hello %s", "world")
        line = capsys.readouterr().out.strip().splitlines()[-1]
        if fmt == "json":
            data = json.loads(line)
            assert (data["corr"], data["wf"], data["agent"], data["message"]) == ("corr-7", "WF-7", "policy", "hello world")
        else:
            assert "corr=corr-7 wf=WF-7 agent=policy" in line and "hello world" in line
