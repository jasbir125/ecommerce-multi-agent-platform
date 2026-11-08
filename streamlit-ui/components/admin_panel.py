"""Admin panel: workflow list, failures, compensation, cost and token usage, RAG quality."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from api_client import ApiError, Identity
from components import kafka_event_viewer
from data import compensation_rows, fmt_ms, fmt_usd, parse_ts, workflow_rows
from state import open_workflow

STATUSES = ["", "STARTED", "INVESTIGATING", "WAITING_FOR_APPROVAL", "READY_FOR_EXECUTION", "COMPLETED", "PARTIALLY_COMPLETED",
            "COMPENSATING", "ESCALATED", "REJECTED", "FAILED"]


def render(api, who: Identity) -> None:
    if not who.is_staff:
        st.warning("The admin panel is for operators and managers. Choose a staff persona in the sidebar.")
        return
    hours = st.select_slider("Look-back window", options=[1, 6, 24, 72, 168], value=24, format_func=lambda h: f"{h} h")
    t_wf, t_fail, t_cost, t_rag, t_events = st.tabs(["Workflows", "Failures and compensation", "Cost and tokens", "RAG quality", "Kafka events"])
    try:
        workflows = api.list_workflows(who)
    except ApiError as e:
        st.error(f"Could not list workflows: {e}")
        workflows = []
    with t_wf:
        _workflows(workflows)
    with t_fail:
        _failures(workflows)
    metrics = None
    try:
        metrics = api.admin_metrics(who, hours)
    except ApiError as e:
        st.error(f"Metrics unavailable: {e}")
    with t_cost:
        _cost(metrics)
    with t_rag:
        _rag(metrics)
    with t_events:
        try:
            kafka_event_viewer.render(api.recent_events(who, 100), key="feed", show_workflow=True)
        except ApiError as e:
            st.error(str(e))


def _workflows(workflows: list[dict]) -> None:
    counts = pd.Series([w["status"] for w in workflows]).value_counts() if workflows else pd.Series(dtype=int)
    cols = st.columns(4)
    cols[0].metric("Workflows", len(workflows))
    cols[1].metric("Completed", int(counts.get("COMPLETED", 0)))
    cols[2].metric("Need attention", int(sum(counts.get(s, 0) for s in ("FAILED", "ESCALATED", "PARTIALLY_COMPLETED", "COMPENSATING"))))
    cols[3].metric("In progress", int(sum(counts.get(s, 0) for s in ("STARTED", "INVESTIGATING", "WAITING_FOR_APPROVAL", "READY_FOR_EXECUTION"))))
    pick = st.selectbox("Filter by status", STATUSES, format_func=lambda s: s or "(all)")
    shown = [w for w in workflows if not pick or w["status"] == pick]
    st.dataframe(pd.DataFrame(workflow_rows(shown)), hide_index=True, width="stretch")
    if shown:
        chosen = st.selectbox("Open a workflow", [w["workflowId"] for w in shown], key="admin-open")
        if st.button("Open in explorer"):
            open_workflow(chosen)
            st.session_state["go_to"] = "Workflow explorer"
            st.rerun()


def _failures(workflows: list[dict]) -> None:
    failed = [w for w in workflows if w["status"] in ("FAILED", "ESCALATED", "REJECTED")]
    st.markdown("##### Failed, escalated and rejected")
    if failed:
        st.dataframe(pd.DataFrame(workflow_rows(failed)), hide_index=True, width="stretch")
    else:
        st.success("None.")
    st.markdown("##### Compensation and partial completion")
    comp = compensation_rows(workflows)
    if comp:
        st.dataframe(pd.DataFrame(comp), hide_index=True, width="stretch")
        st.caption("↩️ marks a compensation step (reserved stock released). The refund stays recorded; support has been alerted.")
    else:
        st.success("No workflow has needed compensation.")


def _cost(m: dict | None) -> None:
    if not m:
        return
    t = m["totals"]
    runs = [r for r in m["runs"] if r.get("tokens")]          # runs with usage recorded
    c = st.columns(4)
    c[0].metric("Runs measured", len(runs))
    c[1].metric("Tokens", f"{t['tokens']:,.0f}")
    c[2].metric("Estimated cost", fmt_usd(t["costUsd"]))
    c[3].metric("Cost per run", fmt_usd(t["costUsd"] / len(runs)) if runs else "-")
    if runs:
        df = pd.DataFrame(runs)
        df["at"] = df["at"].map(lambda v: parse_ts(v).strftime("%m-%d %H:%M:%S") if parse_ts(v) else "")
        st.altair_chart(alt.Chart(df).mark_bar().encode(x=alt.X("workflow_id:N", title=None, sort=None), y=alt.Y("tokens:Q"),
                                                        color=alt.Color("status:N"), tooltip=["workflow_id", "status", "tokens", "cost_usd"])
                        .properties(height=240), width="stretch")
        st.dataframe(df[["workflow_id", "at", "status", "tokens", "cost_usd", "guardrail_rejections", "node_errors"]]
                     .rename(columns={"workflow_id": "workflow", "cost_usd": "cost (USD)"}), hide_index=True, width="stretch")
    else:
        st.info("No token data yet: it is recorded when a run finishes.")


def _rag(m: dict | None) -> None:
    if not m:
        return
    r = m["rag"]
    c = st.columns(6)
    c[0].metric("Policy queries", f"{r['queries']:,}")
    c[1].metric("Abstained", f"{r['abstain_rate']:.0%}", help="Questions the policy could not answer. Review them below.")
    c[2].metric("Cache hit rate", f"{r['cache_hit_rate']:.0%}")
    c[3].metric("Latency p50", fmt_ms(r["p50_ms"]))
    c[4].metric("Latency p95", fmt_ms(r["p95_ms"]))
    c[5].metric("Degraded searches", int(r["degraded"]), help="Keyword-only searches because embeddings were unavailable.")
    series = m.get("ragSeries") or []
    if series:
        df = pd.DataFrame(series).melt(id_vars="hour", value_vars=["queries", "abstained", "cache_hits"])
        st.altair_chart(alt.Chart(df).mark_line(point=True).encode(x=alt.X("hour:T", title=None), y="value:Q", color="variable:N",
                                                                   tooltip=["hour", "variable", "value"]).properties(height=240), width="stretch")
    st.markdown("##### Recent questions the policy could not answer")
    st.caption("Each is either out of scope (fine) or a gap in the policy documents or the retrieval (add a golden-set case).")
    ab = m.get("ragAbstainedQueries") or []
    if ab:
        df = pd.DataFrame(ab).rename(columns={"query_preview": "question", "created_at": "when"})
        df["when"] = df["when"].map(lambda v: (parse_ts(v).strftime("%H:%M:%S") if parse_ts(v) else ""))
        st.dataframe(df, hide_index=True, width="stretch")
    else:
        st.success("No abstentions in this window.")
