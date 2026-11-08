"""Order Recovery Platform: internal demo and operations UI."""
import logging
import time

import streamlit as st

from api_client import ApiError, Identity, SessionExpired
from config import Config
from components import admin_panel, approval_panel, chat, policy_assistant, workflow_view
from data import icon
from state import get_api, open_workflow, selected_workflow

if not logging.getLogger("ui").handlers:                  # one handler for all the UI's loggers, set up once per process
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-5s [streamlit-ui] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
    logging.getLogger("ui").addHandler(_h)
    logging.getLogger("ui").setLevel(logging.INFO)
    logging.getLogger("ui").propagate = False

st.set_page_config(page_title="Order Recovery Platform", page_icon="📦", layout="wide")

PERSONAS = {"customer": "Customer", "operator": "Operator", "manager": "Manager"}


def auth_mode() -> str:
    return st.session_state.get("_auth_mode") or Config.from_env().auth_mode


def persona_from_roles(roles: list[str]) -> str:
    roles = {r.upper() for r in roles}
    return "manager" if "MANAGER" in roles else "operator" if roles & {"OPERATOR", "ADMIN"} else "customer"


def login_sidebar(api) -> Identity | None:
    """Real sign-in: the user gets a short-lived signed token, which is the only credential this UI holds."""
    st.sidebar.title("📦 Order Recovery")
    login = st.session_state.get("auth")
    if login and time.time() >= login["expires_at"]:
        st.session_state.pop("auth")
        st.session_state["login_notice"] = "Your session expired. Please sign in again."
        login = None
    if not login:
        with st.sidebar.form("login"):
            st.markdown("**Sign in**")
            user = st.text_input("Username")
            pw = st.text_input("Password", type="password")
            go = st.form_submit_button("Sign in", type="primary")
        if go:
            try:
                t = api.login(user.strip(), pw)
            except ApiError as e:
                st.session_state["login_notice"] = {"INVALID_CREDENTIALS": "Wrong username or password.",
                                                    "TOO_MANY_ATTEMPTS": "Too many failed attempts. Try again in a few minutes."}.get(e.code, f"Could not sign in: {e}")
            else:
                st.session_state["auth"] = {"token": t["accessToken"], "username": t["username"], "customer_id": t["customerId"] or None,
                                             "roles": t["roles"], "expires_at": time.time() + t["expiresIn"] - 5}
                st.session_state.pop("login_notice", None)
                st.rerun()
        return None
    persona = persona_from_roles(login["roles"])
    who = Identity(persona, login["customer_id"], login["username"], token=login["token"])
    st.session_state["identity"] = who
    left = int(login["expires_at"] - time.time())
    st.sidebar.markdown(f"Signed in as **{login['username']}** ({PERSONAS[persona]})")
    st.sidebar.caption(f"Session expires in {left // 60} min")
    if st.sidebar.button("Sign out"):
        st.session_state.pop("auth", None)
        st.session_state.pop("workflow_id", None)
        st.rerun()
    return who


def sign_in_page() -> None:
    st.title("Multi-Agent Order Recovery")
    notice = st.session_state.get("login_notice")
    (st.warning if notice else st.info)(notice or "Sign in from the sidebar to continue.")
    if Config.from_env().show_demo_credentials:
        with st.expander("Demo accounts (local development only)", expanded=True):
            st.markdown("Password for all of them: `demo-pass`\n\n| user | role |\n|---|---|\n| alice | customer `CUST-1001` |\n"
                        "| bob | customer `CUST-2002` |\n| olivia | operator |\n| manny | manager (can approve) |\n| admin | admin |")


def sidebar() -> Identity:
    st.sidebar.title("📦 Order Recovery")
    persona = st.sidebar.radio("Signed in as", list(PERSONAS), format_func=PERSONAS.get, key="persona",
                               help="Demo personas. The services enforce ownership and roles themselves.")
    cid = None
    if persona == "customer":
        cid = st.sidebar.text_input("Your customer ID", value=st.session_state.get("customer_id", "CUST-1001"), key="customer_id")
    name = {"customer": cid or "customer", "operator": "operator-1", "manager": "manager-1"}[persona]
    who = Identity(persona, cid, name)
    st.session_state["identity"] = who

    with st.sidebar.expander("Open a workflow"):
        wf = st.text_input("Workflow ID", placeholder="WF-…", key="open_wf")
        if st.button("Open", key="open_btn") and wf.strip():
            open_workflow(wf)
            st.session_state["go_to"] = "Workflow explorer"
    with st.sidebar.expander("Service status"):
        for svc, up in get_api().health().items():
            st.write(f"{'🟢' if up else '🔴'} {svc}")
    return who


def explorer(api, who: Identity) -> None:
    wf = selected_workflow()
    if not wf:
        st.info("Choose a workflow from **Customer request**, **Approvals**, **Admin**, or the sidebar.")
        return
    st.subheader(f"Workflow {wf}")
    workflow_view.render(api, who, wf, key="explorer")


def apply_query_params() -> None:
    """Deep links: ?as=manager&wf=WF-123&page=Approvals opens the app as that persona on that workflow/page (first load only)."""
    if st.session_state.get("_qp_applied"):
        return
    st.session_state["_qp_applied"] = True
    qp = st.query_params
    if qp.get("as") in PERSONAS and auth_mode() == "dev":
        st.session_state["persona"] = qp["as"]
    if qp.get("wf"):
        open_workflow(qp["wf"])
        st.session_state["go_to"] = qp.get("page", "Workflow explorer")
    elif qp.get("page"):
        st.session_state["go_to"] = qp["page"]


def main() -> None:
    apply_query_params()
    api = get_api()
    if auth_mode() == "login":
        who = login_sidebar(api)
        if who is None:
            sign_in_page()
            return
        with st.sidebar.expander("Open a workflow"):
            wf = st.text_input("Workflow ID", placeholder="WF-…", key="open_wf")
            if st.button("Open", key="open_btn") and wf.strip():
                open_workflow(wf)
                st.session_state["go_to"] = "Workflow explorer"
        with st.sidebar.expander("Service status"):
            for svc, up in api.health().items():
                st.write(f"{'🟢' if up else '🔴'} {svc}")
    else:
        who = sidebar()
    pages = {"Customer request": ("📝", lambda: chat.render(api, who)),
             "Policy assistant": ("📚", lambda: policy_assistant.render(api, who)),
             "Workflow explorer": ("🔍", lambda: explorer(api, who))}
    if who.is_staff:
        pages["Approvals"] = ("✅", lambda: approval_panel.render(api, who))
        pages["Admin"] = ("🛠", lambda: admin_panel.render(api, who))
    if selected_workflow():
        st.query_params["wf"] = selected_workflow()           # keep the URL shareable
    names = list(pages)
    if st.session_state.get("go_to") in names:
        st.session_state["page"] = st.session_state.pop("go_to")
    if st.session_state.get("page") not in names:
        st.session_state["page"] = names[0]
    page = st.segmented_control("Page", names, key="page", format_func=lambda n: f"{pages[n][0]} {n}", label_visibility="collapsed")
    st.title("Multi-Agent Order Recovery")
    try:
        (pages.get(page) or pages[names[0]])[1]()
    except SessionExpired:
        st.session_state.pop("auth", None)
        st.session_state["login_notice"] = "Your session expired. Please sign in again."
        st.rerun()


main()
