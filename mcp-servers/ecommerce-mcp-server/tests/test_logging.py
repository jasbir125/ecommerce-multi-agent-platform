"""MCP server logging: masking, correlated format, and the log line for an outgoing call to a Spring service."""
import logging

import httpx

from mcp_common import logsetup
from mcp_common.spring import SpringClient


def test_clean_masks_secrets_and_personal_data():
    out = logsetup.clean({"order_id": "ORD-1", "password": "pw", "note": "alice@example.com", "x": "Bearer abcdefghijkl"})
    assert "ORD-1" in out and "pw\"" not in out and "alice@example.com" not in out and "abcdefghijkl" not in out


def test_text_format_shows_correlation_ids(capsys, monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "text")
    logsetup.setup_logging("ecommerce-mcp-server")
    t = (logsetup.corr.set("corr-9"), logsetup.wf.set("WF-9"), logsetup.customer.set("CUST-1"))
    try:
        logging.getLogger("mcp.tool").info("TOOL IN  get_order_status args={}")
    finally:
        for var, tok in zip((logsetup.corr, logsetup.wf, logsetup.customer), t):
            var.reset(tok)
    line = capsys.readouterr().out
    assert "corr=corr-9 wf=WF-9 customer=CUST-1" in line and "TOOL IN  get_order_status" in line


async def test_spring_client_logs_each_call_with_status_and_duration(caplog):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"orderId": "ORD-1", "status": "SHIPPED", "token": "s3cret"}))
    client = SpringClient("http://order", "tok", transport=transport)
    with caplog.at_level(logging.INFO, logger="mcp.spring"):
        await client.get("/api/orders/ORD-1")
    line = next(r.getMessage() for r in caplog.records if r.name == "mcp.spring")
    assert line.startswith("=> GET /api/orders/ORD-1 200") and "SHIPPED" in line and "s3cret" not in line
