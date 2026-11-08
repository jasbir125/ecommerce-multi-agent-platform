"""Workflow timeline: when each agent step ran, for how long, with what status, errors and retries."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from data import fmt_ms


def render(rows: list[dict]) -> None:
    if not rows:
        st.info("No steps recorded yet.")
        return
    df = pd.DataFrame(rows)
    order = list(dict.fromkeys(df.sort_values("start_ms")["step"]))
    chart = (alt.Chart(df).mark_bar(cornerRadius=3, height=18)
             .encode(x=alt.X("start_ms:Q", title="milliseconds since the run started"), x2="end_ms:Q",
                     y=alt.Y("step:N", sort=order, title=None, axis=alt.Axis(labelOverlap=False, labelLimit=200)),
                     color=alt.Color("status:N", legend=None, scale=alt.Scale(domain=["ok", "error"], range=["#2563eb", "#dc2626"])),
                     tooltip=["step", "status", "latency_ms", "retries", "tool_calls", "tokens", "error"])
             .properties(height=max(120, 30 * len(order))))
    st.altair_chart(chart, width="stretch")
    st.caption("Bars that overlap ran in parallel.")
    table = pd.DataFrame([{"": r["icon"], "Step": r["step"], "Status": r["status"], "Started +": fmt_ms(r["start_ms"]),
                           "Took": fmt_ms(r["latency_ms"]), "Retries": r["retries"], "Tools": r["tool_calls"],
                           "Tokens": r["tokens"], "Error": r["error"], "Note": r["detail"][:90]} for r in rows])
    st.dataframe(table, hide_index=True, width="stretch")
    for r in (r for r in rows if r["error"]):
        st.error(f"{r['step']}: {r['error']}")
