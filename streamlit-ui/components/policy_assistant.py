"""Ask a policy question directly and see the grounded, cited answer."""
from __future__ import annotations

import streamlit as st

from api_client import ApiError, Identity
from components import rag_evidence


def render(api, who: Identity) -> None:
    st.caption("Ask the same policy pipeline the agents use. Answers are retrieved, cited and checked; if policy cannot answer, it says so.")
    with st.form("ask"):
        q = st.text_input("Question", value="My package is five days late, can I get my money back?")
        c1, c2, c3 = st.columns(3)
        region = c1.selectbox("Customer region", ["", "EU"], format_func=lambda x: x or "(not set)")
        category = c2.selectbox("Product category", ["", "electronics"], format_func=lambda x: x or "(not set)")
        as_of = c3.text_input("As of date (YYYY-MM-DD)", value="", help="Answer from the policy version that was in force on this date.")
        go = st.form_submit_button("Ask", type="primary")
    if go and q.strip():
        with st.spinner("Searching policy and checking the answer…"):
            try:
                st.session_state["rag_result"] = api.ask_policy(who, q.strip(), region=region, productCategory=category, asOfDate=as_of.strip())
            except ApiError as e:
                st.error(f"Could not answer: {e}")
                st.session_state.pop("rag_result", None)
    if st.session_state.get("rag_result"):
        rag_evidence.render(st.session_state["rag_result"], key="ask")
