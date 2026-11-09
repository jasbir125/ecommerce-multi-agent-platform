"""Shipment Agent: carrier facts only; an unreachable carrier means unverified, never a guess."""
import json

import pytest

from app.agents import shipment_agent
from app.guardrails.tool_guardrails import ToolCallError
from app.llm import BudgetExceeded
from tests.agent_fakes import FakeGateway, make_deps, make_state, patch_react

SHIPMENT = {"orderId": "ORD-7842", "carrier": "DHL", "trackingNo": "T1", "status": "LOST", "delayDays": 5, "exceptionType": "LOST",
            "lastEvent": "no scan"}


def tool_call(payload=SHIPMENT):
    return {"tool": "get_shipment_status", "ok": True, "result": json.dumps(payload)}


async def run(monkeypatch, gateway=None, calls=None, error=None, state=None):
    patch_react(monkeypatch, shipment_agent, tool_calls=calls, error=error)
    deps, workflows = make_deps(gateway or FakeGateway({"get_shipment_status": SHIPMENT}))
    return await shipment_agent.run(state or make_state(), deps, {}), workflows


async def test_happy_path_reports_the_lost_parcel(monkeypatch):
    out, _ = await run(monkeypatch, FakeGateway(), calls=[tool_call()])
    s = out["shipment"]
    assert s.verified and s.exception_type == "LOST" and s.delay_days == 5
    assert out["evidence"] == ["shipment LOST, 5 days late, exception=LOST"] and out["errors"] == []


async def test_missing_order_id_skips_the_agent(monkeypatch):
    out, _ = await run(monkeypatch, state=make_state(order_id=""))
    assert out["errors"] == ["SHIPMENT_SKIPPED_MISSING_ORDER_ID"] and "shipment" not in out


async def test_unreachable_carrier_gives_an_unverified_snapshot_and_an_audit_event(monkeypatch):
    out, workflows = await run(monkeypatch, FakeGateway({"get_shipment_status": ToolCallError("SHIPMENT_UNAVAILABLE: carrier down")}))
    assert out["shipment"].verified is False and out["errors"][0].startswith("SHIPMENT_UNVERIFIED")
    assert workflows.events() == ["SHIPMENT_UNVERIFIED"]


async def test_timeout_is_an_unverified_shipment(monkeypatch):
    out, _ = await run(monkeypatch, FakeGateway({"get_shipment_status": TimeoutError("slow")}))
    assert out["shipment"].verified is False


async def test_model_failure_is_recorded_and_the_direct_read_verifies(monkeypatch):
    out, _ = await run(monkeypatch, error=TimeoutError("model"))
    assert "SHIPMENT_AGENT_ERROR: TimeoutError" in out["errors"] and out["shipment"].verified


async def test_direct_read_is_noted(monkeypatch):
    out, _ = await run(monkeypatch)
    assert "SHIPMENT_LOOKUP_DIRECT" in out["errors"]


async def test_a_spent_budget_propagates(monkeypatch):
    with pytest.raises(BudgetExceeded):
        await run(monkeypatch, error=BudgetExceeded("budget"))


@pytest.mark.parametrize("payload", [{**SHIPMENT, "orderId": "ORD-OTHER"}, {**SHIPMENT, "delayDays": -2}, {**SHIPMENT, "delayDays": "many"},
                                     {**SHIPMENT, "exceptionType": "TELEPORTED"}])
async def test_inconsistent_carrier_data_is_unverified(monkeypatch, payload):
    out, _ = await run(monkeypatch, FakeGateway({"get_shipment_status": payload}))
    assert out["shipment"].verified is False


async def test_no_exception_defaults_to_none(monkeypatch):
    payload = {k: v for k, v in SHIPMENT.items() if k != "exceptionType"}
    out, _ = await run(monkeypatch, FakeGateway({"get_shipment_status": payload}))
    assert out["shipment"].exception_type == "NONE"


async def test_the_agent_never_creates_a_carrier_case(monkeypatch):
    gw = FakeGateway({"get_shipment_status": SHIPMENT})
    await run(monkeypatch, gw)
    assert "create_carrier_case" not in gw.names()
