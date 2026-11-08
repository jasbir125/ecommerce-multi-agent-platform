"""Evaluation hook: after each run, store simple quality metrics in evaluation_result."""
from __future__ import annotations

import json
import logging

from app.models.workflow_state import WorkflowState

log = logging.getLogger("agent.eval")


def metrics_for(state: WorkflowState) -> dict[str, float | bool]:
    guardrail_hits = sum(1 for e in state.errors if e.startswith(("GUARDRAIL", "INPUT_", "OUTPUT_")))
    tool_errors = sum(1 for t in state.trace if t.get("status") == "error")
    return {
        "rag_confidence": state.policy.rag_confidence if state.policy else 0.0,
        "guardrail_rejections": float(guardrail_hits),
        "node_errors": float(tool_errors),
        "completed": state.status == "COMPLETED",
        "escalated": state.status in ("ESCALATED", "REJECTED"),
    }


class RunEvaluator:
    def __init__(self, database_url: str):
        self._url = database_url

    async def record(self, state: WorkflowState, usage: dict) -> None:
        import asyncpg
        rows = {**metrics_for(state), "total_tokens": float(usage.get("total_tokens", 0)),
                "cost_usd": float(usage.get("cost_usd", 0))}
        try:
            conn = await asyncpg.connect(self._url)
            try:
                for metric, value in rows.items():
                    score = float(value)
                    await conn.execute(
                        "INSERT INTO evaluation_result (workflow_id, dataset, metric, score, passed, detail) "
                        "VALUES ($1, 'online', $2, $3, $4, $5::jsonb)",
                        state.workflow_id, metric, min(score, 9999.0), None, json.dumps({"status": state.status}))
            finally:
                await conn.close()
        except Exception as e:  # noqa: BLE001 - evaluation must never fail a run
            log.warning("could not store evaluation metrics: %s", e)
