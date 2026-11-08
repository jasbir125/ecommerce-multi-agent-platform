"""Operations metrics for the admin panel: run cost/usage and RAG quality, straight from the platform's own tables."""
from __future__ import annotations

from typing import Any

import asyncpg

from app.config import Settings

RUNS_SQL = """
SELECT workflow_id,
       max(created_at) AS at,
       max(detail->>'status') AS status,
       max(score) FILTER (WHERE metric = 'total_tokens') AS tokens,
       max(score) FILTER (WHERE metric = 'cost_usd') AS cost_usd,
       max(score) FILTER (WHERE metric = 'rag_confidence') AS rag_confidence,
       max(score) FILTER (WHERE metric = 'guardrail_rejections') AS guardrail_rejections,
       max(score) FILTER (WHERE metric = 'node_errors') AS node_errors
FROM evaluation_result WHERE dataset = 'online' AND workflow_id IS NOT NULL
GROUP BY workflow_id ORDER BY max(created_at) DESC LIMIT $1"""

RAG_SQL = """
SELECT count(*) AS queries,
       coalesce(round(avg(latency_ms)), 0) AS avg_ms,
       coalesce(percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms), 0) AS p50_ms,
       coalesce(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms), 0) AS p95_ms,
       count(*) FILTER (WHERE cache_hit) AS cache_hits,
       count(*) FILTER (WHERE insufficient) AS abstained,
       count(*) FILTER (WHERE degraded) AS degraded,
       coalesce(round(avg(confidence) FILTER (WHERE NOT insufficient), 3), 0) AS avg_confidence
FROM rag_query_log WHERE created_at > now() - make_interval(hours => $1)"""

ABSTAINED_SQL = """
SELECT query_preview, confidence, created_at FROM rag_query_log
WHERE insufficient AND NOT cache_hit AND created_at > now() - make_interval(hours => $1)
ORDER BY created_at DESC LIMIT 15"""

SERIES_SQL = """
SELECT date_trunc('hour', created_at) AS hour, count(*) AS queries,
       count(*) FILTER (WHERE insufficient) AS abstained, count(*) FILTER (WHERE cache_hit) AS cache_hits
FROM rag_query_log WHERE created_at > now() - make_interval(hours => $1)
GROUP BY 1 ORDER BY 1"""


def _num(v: Any) -> Any:
    return float(v) if v is not None and not isinstance(v, (int, float, str)) else v


def _rows(rows) -> list[dict[str, Any]]:
    return [{k: (_num(v) if not hasattr(v, "isoformat") else v.isoformat()) for k, v in dict(r).items()} for r in rows]


async def metrics(settings: Settings, hours: int = 24, limit: int = 50) -> dict[str, Any]:
    conn = await asyncpg.connect(settings.database_url, timeout=5)
    try:
        runs = _rows(await conn.fetch(RUNS_SQL, limit))
        rag = _rows(await conn.fetch(RAG_SQL, hours))[0]
        abstained = _rows(await conn.fetch(ABSTAINED_SQL, hours))
        series = _rows(await conn.fetch(SERIES_SQL, hours))
    finally:
        await conn.close()
    q = rag["queries"] or 0
    rag.update(cache_hit_rate=round(rag["cache_hits"] / q, 3) if q else 0.0, abstain_rate=round(rag["abstained"] / q, 3) if q else 0.0)
    by_status: dict[str, int] = {}
    for r in runs:
        by_status[r["status"] or "UNKNOWN"] = by_status.get(r["status"] or "UNKNOWN", 0) + 1
    return {
        "windowHours": hours,
        "runs": runs,
        "totals": {"runs": len(runs), "tokens": sum(r["tokens"] or 0 for r in runs), "costUsd": round(sum(r["cost_usd"] or 0 for r in runs), 6),
                   "byStatus": by_status, "guardrailRejections": sum(r["guardrail_rejections"] or 0 for r in runs)},
        "rag": rag, "ragAbstainedQueries": abstained, "ragSeries": series,
    }
