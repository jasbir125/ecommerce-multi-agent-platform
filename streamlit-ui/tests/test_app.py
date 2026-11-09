"""Headless renders of the real app with a fake API: every page, the main actions, and the failure paths."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

from tests.samples import FakeApi

APP = str(Path(__file__).parent.parent / "app.py")


def app(persona="customer", page=None, api=None, **state):
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["_api_override"] = api or FakeApi()
    at.session_state["persona"] = persona
    if page:
        at.session_state["page"] = page
    for k, v in state.items():
        at.session_state[k] = v
    return at.run()


def texts(at):
    return " ".join([m.value for m in at.markdown] + [e.value for e in at.error] + [w.value for w in at.warning] +
                    [s.value for s in at.success] + [i.value for i in at.info] + [c.value for c in at.caption])


def labels(at):
    return {m.label for m in at.metric}


def page_options(at):
    return [o.content for o in at.get("segmented_control")[0].proto.options]


def test_customer_sees_request_policy_and_explorer_but_not_staff_pages():
    at = app("customer")
    assert not at.exception
    assert page_options(at) == ["Customer request", "Policy assistant", "Workflow explorer", "Request log"]


def test_staff_also_get_approvals_and_admin():
    for persona in ("operator", "manager"):
        at = app(persona)
        assert len(page_options(at)) == 6 and "Admin" in page_options(at)


def test_request_form_submits_a_live_run_and_shows_the_workflow():
    api = FakeApi()
    at = app("customer", api=api)
    at.text_input[0].set_value("CUST-1001"); at.text_input[1].set_value("ORD-7842")
    at.button[0].click().run()
    assert not at.exception, at.exception
    assert "stream_run" in api.calls and at.session_state["workflow_id"] == "WF-TEST000001"
    assert "Waiting for a manager" in texts(at)             # the paused run is shown clearly
    assert {"Refund", "Replacement", "Risk"} <= labels(at)


def test_request_form_validates_required_fields():
    at = app("customer")
    at.text_input[1].set_value("")
    at.button[0].click().run()
    assert any("required" in e.value for e in at.error)


def test_queued_mode_creates_the_workflow_through_the_workflow_service():
    api = FakeApi()
    at = app("customer", api=api)
    at.radio[0].set_value("Queued (via Kafka)")
    at.button[0].click().run()
    assert "create_workflow" in api.calls and at.session_state["workflow_id"] == "WF-QUEUED0001"


def test_a_failing_service_shows_an_error_instead_of_crashing():
    api = FakeApi(); api.fail = {"stream_run"}
    at = app("customer", api=api)
    at.button[0].click().run()
    assert not at.exception and any("Request failed" in e.value for e in at.error)


def test_workflow_explorer_renders_every_tab():
    at = app("operator", page="Workflow explorer", workflow_id="WF-TEST000001")
    assert not at.exception, at.exception
    assert len(at.tabs) >= 5
    assert {"Confidence", "Grounding", "Sources cited", "Total tokens", "Estimated cost"} <= labels(at)
    body = texts(at)
    assert "refund-policy" in body or any("refund-policy" in str(d.value) for d in at.dataframe)


def test_explorer_without_a_selection_explains_what_to_do():
    at = app("operator", page="Workflow explorer")
    assert "Choose a workflow" in texts(at)


def test_explorer_handles_a_missing_agent_run():
    api = FakeApi(); api.get_run = lambda who, wf: None
    api.run_trace = lambda who, wf: None
    at = app("operator", page="Workflow explorer", workflow_id="WF-TEST000001", api=api)
    assert not at.exception and "Queued" in texts(at)


def test_explorer_reports_a_workflow_that_cannot_be_opened():
    api = FakeApi(); api.fail = {"get_workflow"}
    at = app("customer", page="Workflow explorer", workflow_id="WF-X", api=api)
    assert not at.exception and any("Cannot open" in e.value for e in at.error)


def test_manager_can_approve_and_the_decision_reaches_the_service():
    api = FakeApi()
    at = app("manager", page="Approvals", api=api)
    assert not at.exception, at.exception
    approve = next(b for b in at.button if b.label == "Approve")
    assert not approve.disabled
    approve.click().run()
    assert api.decisions == [("APR-TEST000001", "APPROVED", "")]


def test_manager_can_reject_with_a_comment():
    api = FakeApi()
    at = app("manager", page="Approvals", api=api)
    at.text_input(key="c-APR-TEST000001").set_value("amount too high")
    next(b for b in at.button if b.label == "Reject").click().run()
    assert api.decisions == [("APR-TEST000001", "REJECTED", "amount too high")]


def test_operator_cannot_decide_approvals():
    at = app("operator", page="Approvals")
    assert all(b.disabled for b in at.button if b.label in ("Approve", "Reject"))
    assert "manager" in texts(at)


def test_a_failed_decision_is_shown():
    api = FakeApi(); api.fail = {"decide"}
    at = app("manager", page="Approvals", api=api)
    next(b for b in at.button if b.label == "Approve").click().run()
    assert any("Could not record" in e.value for e in at.error)


def test_admin_panel_shows_cost_rag_quality_and_failures():
    at = app("operator", page="Admin")
    assert not at.exception, at.exception
    assert {"Workflows", "Tokens", "Estimated cost", "Policy queries", "Abstained", "Cache hit rate"} <= labels(at)
    assert len(at.tabs) >= 5


def test_admin_panel_survives_metrics_being_down():
    api = FakeApi(); api.fail = {"admin_metrics"}
    at = app("operator", page="Admin", api=api)
    assert not at.exception and any("Metrics unavailable" in e.value for e in at.error)


def _admin_as_customer():
    from api_client import Identity
    from components import admin_panel
    from tests.samples import FakeApi
    admin_panel.render(FakeApi(), Identity("customer", "CUST-1001"))


def test_customers_are_told_the_admin_panel_is_not_for_them():
    # the page is not offered to customers; calling it directly is still refused politely
    at = AppTest.from_function(_admin_as_customer).run()
    assert any("for operators" in w.value for w in at.warning)


def test_policy_assistant_asks_and_shows_evidence():
    api = FakeApi()
    at = app("customer", page="Policy assistant", api=api)
    at.button[0].click().run()
    assert not at.exception, at.exception
    assert any(c[0] == "ask" for c in api.calls if isinstance(c, tuple))
    assert {"Confidence", "Grounding"} <= labels(at)


def test_policy_assistant_shows_errors():
    api = FakeApi(); api.fail = {"ask_policy"}
    at = app("customer", page="Policy assistant", api=api)
    at.button[0].click().run()
    assert any("Could not answer" in e.value for e in at.error)


def test_deep_links_open_the_right_persona_page_and_workflow():
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["_api_override"] = FakeApi()
    at.query_params["as"] = "manager"
    at.query_params["wf"] = "WF-TEST000001"
    at.query_params["page"] = "Approvals"
    at.run()
    assert not at.exception
    assert at.session_state["persona"] == "manager" and at.session_state["page"] == "Approvals" and at.session_state["workflow_id"] == "WF-TEST000001"
    assert at.query_params["wf"] == "WF-TEST000001"                      # the URL stays shareable


def test_an_unknown_persona_in_the_link_is_ignored():
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["_api_override"] = FakeApi()
    at.query_params["as"] = "superuser"
    at.run()
    assert at.session_state["persona"] == "customer"


class FakeLogs:
    def __init__(self, reason=None, rows=None):
        self.reason, self.rows, self.calls = reason, rows if rows is not None else [], []

    def unavailable_reason(self): return self.reason
    def journey(self, wf, since): self.calls.append(wf); return self.rows, {wf, "corr-1"}


def _row(ts, service, level, text):
    from log_source import Row
    return Row(f"2026-11-09T06:20:{ts}Z", service, level, text, text)


ROWS = [_row("03.100", "workflow-service", "INFO", "http.access: -> POST /api/workflows caller=customer=CUST-1001 req={...}"),
        _row("03.900", "agent-host", "INFO", "[supervisor] agent.graph: AGENT > supervisor received input={}"),
        _row("04.500", "ecommerce-mcp-server", "INFO", "TOOL IN get_order_status args={'order_id': 'ORD-7842'}"),
        _row("09.000", "workflow-service", "ERROR", "http.access: <- POST /api/workflows 500 12ms resp={...}")]


def test_request_log_page_shows_every_line_filters_and_the_download():
    logs = FakeLogs(rows=ROWS)
    at = app("customer", page="Request log", logs=None, _logs_override=logs, workflow_id="WF-TEST000001")
    assert not at.exception, at.exception
    assert logs.calls and {"Log lines", "Services involved", "Ids followed"} <= labels(at)
    assert next(m for m in at.metric if m.label == "Log lines").value == "4"
    assert "Showing 4 of 4 lines" in texts(at)
    at.checkbox(key="log_exchange").check().run()                   # requests and responses only: the AGENT/TOOL/HTTP lines all qualify
    assert "Showing 4 of 4 lines" in texts(at)
    at.text_input(key="log_search").set_value("tool in").run()
    assert "Showing 1 of 4 lines" in texts(at)


def test_request_log_page_explains_when_the_logs_cannot_be_read():
    at = app("customer", page="Request log", _logs_override=FakeLogs(reason="ConnectError: no socket"))
    assert not at.exception
    assert "not reachable" in texts(at) and "make trace" in texts(at)


def test_request_log_page_asks_for_a_workflow_when_none_is_open():
    at = app("customer", page="Request log", _logs_override=FakeLogs())
    assert "Place an order" in texts(at)
