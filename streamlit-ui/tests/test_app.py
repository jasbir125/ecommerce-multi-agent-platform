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
    assert page_options(at) == ["Customer request", "Policy assistant", "Workflow explorer"]


def test_staff_also_get_approvals_and_admin():
    for persona in ("operator", "manager"):
        at = app(persona)
        assert len(page_options(at)) == 5 and "Admin" in page_options(at)


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


# ------------------------------------------------------------------ login mode
def login_app(api=None, **state):
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["_api_override"] = api or FakeApi()
    at.session_state["_auth_mode"] = "login"
    for k, v in state.items():
        at.session_state[k] = v
    return at.run()


def sign_in(at, user, password="demo-pass"):
    at.text_input[0].set_value(user)
    at.text_input[1].set_value(password)
    next(b for b in at.button if b.label == "Sign in").click().run()
    return at


def test_login_mode_asks_for_sign_in_before_showing_anything():
    at = login_app()
    assert not at.exception and "Sign in" in texts(at) and "Demo accounts" in " ".join(e.label for e in at.expander)
    assert not at.get("segmented_control")                                   # no pages until signed in


def test_signing_in_as_a_customer_shows_customer_pages_only():
    at = sign_in(login_app(), "alice")
    assert not at.exception
    assert [o.content for o in at.get("segmented_control")[0].proto.options] == ["Customer request", "Policy assistant", "Workflow explorer"]
    assert at.session_state["identity"].customer_id == "CUST-1001" and at.session_state["identity"].token == "tok-alice"
    assert at.text_input[0].disabled and at.text_input[0].value == "CUST-1001"      # the id comes from the token, not from typing


def test_a_manager_signs_in_and_can_approve():
    api = FakeApi()
    at = sign_in(login_app(api), "manny")
    assert at.session_state["identity"].persona == "manager" and "Approvals" in [o.content for o in at.get("segmented_control")[0].proto.options]
    at.session_state["page"] = "Approvals"
    at.run()
    next(b for b in at.button if b.label == "Approve").click().run()
    assert api.decisions and api.decisions[0][1] == "APPROVED"


def test_a_wrong_password_is_refused_with_a_clear_message():
    at = sign_in(login_app(), "alice", "wrong")
    assert "auth" not in at.session_state and "Wrong username or password" in texts(at)


def test_an_unknown_user_gets_the_same_message():
    assert "Wrong username or password" in texts(sign_in(login_app(), "nobody"))


def test_staff_cannot_submit_customer_requests():
    at = sign_in(login_app(), "olivia")
    assert "Requests are submitted by customers" in texts(at)
    assert not at.button or all(b.label != "Submit request" for b in at.button)


def test_an_expired_session_returns_to_sign_in():
    at = login_app(auth={"token": "t", "username": "alice", "customer_id": "CUST-1001", "roles": ["CUSTOMER"], "expires_at": 0})
    assert "auth" not in at.session_state and "expired" in texts(at)


def test_the_server_refusing_the_token_signs_the_user_out():
    from api_client import SessionExpired
    api = FakeApi()
    api.get_workflow = lambda who, wf: (_ for _ in ()).throw(SessionExpired("expired"))
    at = sign_in(login_app(api), "alice")
    at.session_state["page"] = "Workflow explorer"
    at.session_state["workflow_id"] = "WF-TEST000001"
    at.run()
    assert "auth" not in at.session_state


def test_deep_link_persona_switching_is_dev_only():
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["_api_override"] = FakeApi()
    at.session_state["_auth_mode"] = "login"
    at.query_params["as"] = "manager"
    at.run()
    assert at.session_state["persona"] != "manager" if "persona" in at.session_state else True
