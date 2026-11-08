"""Kafka event panel: event type, payload, correlation id, timestamp."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from data import event_rows


def render(events: list[dict], key: str = "events", show_workflow: bool = False) -> None:
    rows = event_rows(events)
    if not rows:
        st.info("No events yet.")
        return
    topics = sorted({r["topic"] for r in rows})
    chosen = st.multiselect("Filter by topic", topics, key=f"{key}-topics")
    shown = [r for r in rows if not chosen or r["topic"] in chosen]
    cols = ["time", "topic", "type", "correlation_id", "delivered"] + (["workflow"] if show_workflow else [])
    st.dataframe(pd.DataFrame(shown)[cols].rename(columns={"correlation_id": "correlation id"}), hide_index=True, width="stretch")
    if shown:
        labels = [f"{r['time']}  {r['topic']}  ({r['type']})" for r in shown]
        pick = st.selectbox("Inspect an event's payload", range(len(shown)), format_func=lambda i: labels[i], key=f"{key}-pick")
        ev = shown[pick]
        c1, c2 = st.columns([1, 2])
        c1.markdown(f"**Topic** `{ev['topic']}`  \n**Type** `{ev['type']}`  \n**Correlation** `{ev['correlation_id']}`  \n"
                    f"**Caused by** `{ev['causation_id'] or '-'}`  \n**Event id** `{ev['event_id']}`")
        c2.code(ev["payload"], language="json")
