"""RAG evidence panel: what was retrieved, how it scored, what was used, and whether the answer is grounded."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from data import candidate_rows, fmt_ms, grounding_summary


def render(rag: dict | None, key: str = "rag") -> None:
    if not rag:
        st.info("No policy evidence for this workflow yet (the policy agent has not run, or the request was stopped earlier).")
        return
    g = grounding_summary(rag)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Confidence", f"{rag.get('confidence', 0):.0%}")
    c2.metric("Grounding", f"{g['icon']} {g['label']}")
    cited = rag.get("sources") or []
    c3.metric("Sources cited", len(cited), help="; ".join(f"{x['policyKey']} v{x['version']} ({x.get('section') or ''})" for x in cited) or None)
    c4.metric("Answer time", fmt_ms(g["latency_ms"]) + (" (cached)" if g["cache_hit"] else ""))

    if rag.get("insufficientEvidence"):
        st.warning(rag.get("answer") or "Insufficient evidence.")
        if g["reason"]:
            st.caption(f"Why: {g['reason']}. No business action is taken on missing evidence.")
    else:
        st.success(rag.get("answer", ""))
    if g["degraded"]:
        st.warning("Retrieval ran in degraded mode (keyword search only) because embeddings were unavailable.")

    st.markdown("##### Retrieved passages")
    rows = candidate_rows(rag)
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df.drop(columns=["evidence"]).rename(columns={
            "in_evidence": "shown to model", "used_in_answer": "cited in answer"}),
            hide_index=True, width="stretch",
            column_config={"relevance": st.column_config.ProgressColumn("relevance", min_value=0, max_value=1, format="%.2f"),
                           "reranker": st.column_config.ProgressColumn("reranker", min_value=0, max_value=1, format="%.2f"),
                           "shown to model": st.column_config.CheckboxColumn("shown to model"),
                           "cited in answer": st.column_config.CheckboxColumn("cited in answer")})
    else:
        st.caption("No passages were retrieved.")

    sources = rag.get("sources") or []
    if sources:
        st.markdown("##### Citations")
        for i, s in enumerate(sources, 1):
            with st.expander(f"[{i}] {s['policyKey']} v{s['version']} · {s.get('section') or ''}", expanded=i == 1):
                st.markdown(f"> {s.get('text') or ''}")
                st.caption(f"{s.get('source', '')} · relevance {s.get('score', 0):.2f} · reranker {s.get('rerankScore', 0):.2f}")

    with st.expander("How this answer was produced"):
        if g["timings"]:
            st.markdown("**Time per stage:** " + " · ".join(f"{k} {fmt_ms(v)}" for k, v in g["timings"].items()))
        if g["attempts"]:
            st.markdown(f"**Generation attempts:** {g['attempts']}" + ("  ·  fell back to verbatim evidence" if g["fallback"] else ""))
        if g["score"] is not None:
            st.markdown(f"**Grounding check:** {g['score']:.0%} of statements fully supported")
        for issue in g["issues"]:
            st.error(f"{issue['code']}: {issue.get('detail', '')}")
        if g["dropped"]:
            st.markdown("**Withheld from the model:** " + ", ".join(g["dropped"][:8]))
        if g["corpus_version"] is not None:
            st.caption(f"Policy corpus version {g['corpus_version']}")
