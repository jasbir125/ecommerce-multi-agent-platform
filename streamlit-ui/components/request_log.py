"""Request log: everything the services logged for one request, from the click to the final answer, oldest line first."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from api_client import ApiError, Identity
from data import icon, is_terminal
from state import selected_workflow


def _since(wf: dict) -> int:
    try:
        created = datetime.fromisoformat(str(wf["createdAt"]).replace("Z", "+00:00"))
        created = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        created = datetime.now(timezone.utc) - timedelta(hours=6)
    return int((created - timedelta(seconds=120)).timestamp())


def _seconds(a: str, b: str) -> float:
    def parse(ts: str) -> datetime:
        return datetime.fromisoformat(ts.rstrip("Z")[:26] + "+00:00")
    return (parse(b) - parse(a)).total_seconds()


def render(api, who: Identity, logs) -> None:
    st.caption("Every service's log lines for one request, in time order: what each one received, what it called and what it returned. "
               "Bodies are masked and shortened by the services' own loggers.")
    reason = logs.unavailable_reason()
    if reason:
        st.warning(f"The container logs are not reachable from the UI ({reason}). Run `make trace ID=<workflow id>` in a terminal instead.")
        return
    wf_id = st.text_input("Workflow ID", value=selected_workflow() or "", placeholder="WF-…", key="log_wf").strip()
    if not wf_id:
        st.info("Place an order on **Customer request** (the workflow opens here automatically), or paste a workflow id.")
        return
    try:
        wf = api.get_workflow(who, wf_id)                # the workflow service enforces who may see this case
    except ApiError as e:
        st.error(f"Cannot open {wf_id}: {e}")
        return
    status = st.session_state.get("log-status")
    body = st.fragment(run_every=None if is_terminal(status) else 3)(_body)
    body(api, who, logs, wf_id, wf)


def _body(api, who: Identity, logs, wf_id: str, wf: dict) -> None:
    run = None
    try:
        wf = api.get_workflow(who, wf_id)
        run = api.get_run(who, wf_id)
    except ApiError:
        pass
    state = (run or {}).get("status") or wf["status"]
    st.session_state["log-status"] = state
    st.markdown(f"### {icon(state)} {state.replace('_', ' ').title()}")
    st.caption(f"Workflow `{wf['workflowId']}` · order `{wf['orderId']}` · customer `{wf['customerId']}`"
               + ("" if is_terminal(state) else " · refreshing every 3 s until it finishes"))
    try:
        rows, ids = logs.journey(wf_id, _since(wf))
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not read the container logs: {e}")
        return
    if not rows:
        st.info("No log lines yet for this workflow.")
        return

    services = sorted({r.service for r in rows})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Log lines", len(rows))
    c2.metric("Services involved", len(services))
    c3.metric("Ids followed", len(ids), help=", ".join(sorted(ids)))
    c4.metric("First to last line", f"{_seconds(rows[0].ts, rows[-1].ts):.1f} s")

    all_levels = ["INFO", "WARN", "WARNING", "ERROR", "DEBUG"]
    f1, f2, f3 = st.columns([2, 1, 2])
    picked = f1.multiselect("Services", services, default=services, key="log_services")
    levels = f2.multiselect("Level", all_levels, default=all_levels, key="log_levels")
    needle = f3.text_input("Search", placeholder="e.g. refund, ORD-8801, 404", key="log_search").strip().lower()
    only_exchange = st.checkbox("Requests and responses only (HTTP calls, agent / tool / model calls, Kafka events)", value=False, key="log_exchange")
    shown = [r for r in rows if r.service in picked and r.level in levels and (not only_exchange or r.exchange) and (not needle or needle in r.raw.lower())]
    st.caption(f"Showing {len(shown)} of {len(rows)} lines.")

    st.dataframe(pd.DataFrame([{"time": r.ts[11:23], "service": r.service, "level": r.level, "message": r.text} for r in shown]),
                 hide_index=True, width="stretch", height=520,
                 column_config={"message": st.column_config.TextColumn("message", width="large")})
    full = "\n".join(f"{r.ts[11:23]}  {r.service:<22} {r.level:<5} {r.text}" for r in shown)
    with st.expander("Complete text of the lines above (select, copy or download)"):
        st.code(full, language=None, wrap_lines=True)
    st.download_button("Download as .log", full, file_name=f"{wf_id}.log", key="log_download")
