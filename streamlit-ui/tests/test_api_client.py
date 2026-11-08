import json

import httpx
import pytest

from api_client import ApiClient, ApiError, Identity
from config import Config

CUSTOMER = Identity("customer", "CUST-1001", "cust")
MANAGER = Identity("manager", None, "manager-1")


def client(handler):
    return ApiClient(Config(), transport=httpx.MockTransport(handler))


def test_identity_roles_and_capabilities():
    assert CUSTOMER.roles == "" and not CUSTOMER.is_staff and not CUSTOMER.can_decide
    assert MANAGER.roles == "MANAGER,OPERATOR" and MANAGER.is_staff and MANAGER.can_decide
    assert Identity("operator", None).roles == "OPERATOR" and not Identity("operator", None).can_decide


def test_requests_carry_the_token_and_the_persona():
    seen = {}

    def handler(req):
        seen.update(req.headers)
        return httpx.Response(200, json={"workflowId": "WF-1"})
    client(handler).get_workflow(CUSTOMER, "WF-1")
    assert seen["x-service-token"] == "local-demo-token" and seen["x-customer-id"] == "CUST-1001" and "x-roles" not in seen
    client(handler).list_approvals(MANAGER, "PENDING")
    assert seen["x-roles"] == "MANAGER,OPERATOR"


def test_each_call_goes_to_the_right_service():
    urls = []

    def handler(req):
        urls.append(str(req.url))
        return httpx.Response(200, json=[])
    c = client(handler)
    c.list_workflows(MANAGER, "FAILED"); c.list_approvals(MANAGER, "PENDING"); c.audit(MANAGER, "WF-1"); c.recent_events(MANAGER, 5)
    c.get_run(MANAGER, "WF-1"); c.admin_metrics(MANAGER, 6)
    assert urls[0] == "http://localhost:8088/api/workflows?status=FAILED"
    assert urls[1] == "http://localhost:8085/api/approvals?status=PENDING"
    assert urls[2] == "http://localhost:8087/api/audit/workflows/WF-1"
    assert urls[3] == "http://localhost:8088/api/workflows/events/recent?limit=5"
    assert urls[4] == "http://localhost:8092/api/agent/runs/WF-1" and urls[5].endswith("/api/agent/admin/metrics?hours=6")


def test_decide_sends_who_decided():
    body = {}

    def handler(req):
        body.update(json.loads(req.content))
        return httpx.Response(200, json={})
    client(handler).decide(MANAGER, "APR-1", "APPROVED", "looks right")
    assert body == {"decision": "APPROVED", "decidedBy": "manager-1", "comment": "looks right"}


def test_service_errors_become_api_errors_with_the_service_code():
    def handler(req):
        return httpx.Response(403, json={"code": "NOT_ORDER_OWNER", "detail": "You do not have access to this order"})
    with pytest.raises(ApiError) as e:
        client(handler).get_workflow(CUSTOMER, "WF-1")
    assert e.value.status == 403 and e.value.code == "NOT_ORDER_OWNER" and "do not have access" in str(e.value)


def test_agent_host_style_errors_are_understood_too():
    def handler(req):
        return httpx.Response(409, json={"detail": {"code": "ALREADY_STARTED", "message": "already has a run"}})
    with pytest.raises(ApiError) as e:
        client(handler).get_run(CUSTOMER, "WF-1")
    assert e.value.code == "ALREADY_STARTED" and e.value.message == "already has a run"


def test_a_missing_run_is_none_not_an_error():
    assert client(lambda r: httpx.Response(404, json={"detail": "x"})).get_run(CUSTOMER, "WF-1") is None
    with pytest.raises(ApiError):
        client(lambda r: httpx.Response(404, json={"detail": "x"})).get_workflow(CUSTOMER, "WF-1")


def test_an_unreachable_service_is_reported_clearly():
    def handler(req):
        raise httpx.ConnectError("refused")
    with pytest.raises(ApiError) as e:
        client(handler).get_workflow(CUSTOMER, "WF-1")
    assert e.value.code == "UNREACHABLE" and e.value.status == 0


def test_stream_run_parses_server_sent_events():
    sse = 'data: {"event": "started", "workflowId": "WF-1"}\n\ndata: {"event": "node", "node": "order"}\n\ndata: {"event": "final", "run": {"status": "ESCALATED"}}\n\n'
    seen = {}

    def handler(req):
        seen["url"], seen["body"] = str(req.url), json.loads(req.content)
        return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})
    events = list(client(handler).stream_run(CUSTOMER, "ORD-1", "late"))
    assert [e["event"] for e in events] == ["started", "node", "final"] and events[-1]["run"]["status"] == "ESCALATED"
    assert "stream=true" in seen["url"] and seen["body"] == {"customerId": "CUST-1001", "orderId": "ORD-1", "message": "late"}


def test_stream_errors_surface_as_api_errors():
    with pytest.raises(ApiError) as e:
        list(client(lambda r: httpx.Response(422, json={"detail": {"code": "INPUT_REJECTED_BYPASS_APPROVAL", "message": "refused"}}))
             .stream_run(CUSTOMER, "ORD-1", "skip approval"))
    assert e.value.code == "INPUT_REJECTED_BYPASS_APPROVAL"


def test_health_reports_each_service():
    def handler(req):
        return httpx.Response(200 if "8092" not in str(req.url) else 500)
    assert client(handler).health() == {"workflow-service": True, "approval-service": True, "agent-host": False}


def test_config_reads_the_environment(monkeypatch):
    monkeypatch.setenv("STREAMLIT_API_BASE_URL", "http://wf:1/")
    monkeypatch.setenv("AGENT_HOST_URL", "http://agent:2")
    monkeypatch.setenv("MCP_SERVICE_TOKEN", "tok")
    c = Config.from_env()
    assert c.workflow_url == "http://wf:1" and c.agent_url == "http://agent:2" and c.service_token == "tok"
