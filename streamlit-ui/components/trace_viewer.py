"""Agent trace: supervisor and decision reasoning, specialist calls, MCP tool calls, model calls, tokens, latency."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from data import decision_summary, fmt_ms, fmt_usd, tool_call_rows, usage_rows


def render(run: dict, trace: list[dict]) -> None:
    d = decision_summary(run, trace)
    st.markdown("##### Supervisor and decision")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**Intent:** `{d['intent'] or '-'}`")
        st.markdown("**Plan:** " + (" → ".join(d["plan"]) if d["plan"] else "-"))
        if d["supervisor_reasoning"]:
            st.caption(f"Supervisor: {d['supervisor_reasoning']}")
    with c2:
        if d["action"]:
            st.markdown(f"**Decision:** `{d['action']}`  ·  risk **{d['risk']}**  ·  approval required: **{d['approval_required']}**")
            if d["refund_amount"]:
                st.markdown(f"Refund {d['refund_amount']}" + (f"  ·  replacement `{d['replacement']}`" if d["replacement"] else ""))
            st.caption(d["reason"] or "")
    if d["evidence"]:
        with st.expander("Evidence the decision relied on"):
            for e in d["evidence"]:
                st.write(f"- {e}")

    st.markdown("##### Specialist agents")
    steps = [t for t in trace if t["agent"] in ("order", "shipment", "policy", "product", "risk", "decision", "response")]
    st.dataframe(pd.DataFrame([{"Agent": t["agent"].title(), "Status": t["status"], "Latency": fmt_ms(t.get("latency_ms")),
                                "Tokens": t.get("tokens", 0), "Tool calls": len(t.get("tool_calls") or []),
                                "Retries": t.get("retries", 0), "Result": (t.get("detail") or "")[:100]} for t in steps]),
                 hide_index=True, width="stretch")

    st.markdown("##### MCP tool calls")
    calls = tool_call_rows(trace)
    if calls:
        st.dataframe(pd.DataFrame([{"OK": c["icon"], "Agent": c["agent"], "Tool": c["tool"], "Error": c["error"]} for c in calls]),
                     hide_index=True, width="stretch", column_config={"OK": st.column_config.TextColumn("OK", width="small")})
    else:
        st.caption("No tool calls recorded.")

    st.markdown("##### Model calls, tokens and cost")
    usage = run.get("usage") or {}
    rows = usage_rows(usage)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total tokens", f"{usage.get('total_tokens', 0):,}")
    m2.metric("Estimated cost", fmt_usd(usage.get("cost_usd")))
    m3.metric("Model calls", sum(r["calls"] for r in rows))
    m4.metric("Token budget used", f"{(usage.get('total_tokens', 0) / usage['budget']):.0%}" if usage.get("budget") else "-")
    if rows:
        df = pd.DataFrame(rows)
        long = df.melt(id_vars="agent", value_vars=["input_tokens", "output_tokens"], var_name="kind", value_name="tokens")
        st.altair_chart(alt.Chart(long).mark_bar().encode(x=alt.X("tokens:Q"), y=alt.Y("agent:N", sort="-x", title=None, axis=alt.Axis(labelOverlap=False)),
                                                         color=alt.Color("kind:N", title=None),
                                                         tooltip=["agent", "kind", "tokens"]).properties(height=max(100, 28 * len(df))),
                        width="stretch")
        st.dataframe(df, hide_index=True, width="stretch",
                     column_config={"cost_usd": st.column_config.NumberColumn("cost (USD)", format="$%.5f")})
    st.markdown("##### Latency by step")
    lat = pd.DataFrame([{"step": t["agent"], "ms": t.get("latency_ms", 0)} for t in trace if t.get("latency_ms")])
    if not lat.empty:
        st.altair_chart(alt.Chart(lat).mark_bar().encode(x="ms:Q", y=alt.Y("step:N", sort="-x", title=None, axis=alt.Axis(labelOverlap=False)), tooltip=["step", "ms"])
                        .properties(height=max(100, 24 * len(lat))), width="stretch")
