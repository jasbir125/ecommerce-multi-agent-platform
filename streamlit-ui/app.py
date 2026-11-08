"""Order Recovery Platform: internal demo and operations UI."""
import logging

import streamlit as st

from api_client import Identity
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
    if qp.get("as") in PERSONAS:
        st.session_state["persona"] = qp["as"]
    if qp.get("wf"):
        open_workflow(qp["wf"])
        st.session_state["go_to"] = qp.get("page", "Workflow explorer")
    elif qp.get("page"):
        st.session_state["go_to"] = qp["page"]


def main() -> None:
    apply_query_params()
    api = get_api()
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
    (pages.get(page) or pages[names[0]])[1]()


main()
