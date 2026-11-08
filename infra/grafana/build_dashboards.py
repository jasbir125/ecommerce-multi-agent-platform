"""Generates the Grafana dashboards in infra/grafana/dashboards/. Run: python infra/grafana/build_dashboards.py

Keeping the dashboards as code makes them reviewable; the JSON is committed so Grafana provisions them on start.
Every PromQL expression here is checked against a running Prometheus by infra/grafana/validate_dashboards.py.
"""
import json
from pathlib import Path

OUT = Path(__file__).parent / "dashboards"
PROM = {"type": "prometheus", "uid": "prom"}


class Board:
    def __init__(self, uid: str, title: str, description: str, tags: list[str]):
        self.d = {"uid": uid, "title": title, "description": description, "tags": ["order-recovery", *tags], "timezone": "browser",
                  "schemaVersion": 39, "version": 1, "refresh": "30s", "time": {"from": "now-3h", "to": "now"}, "panels": [],
                  "templating": {"list": []}, "annotations": {"list": []}}
        self._id, self._x, self._y, self._rowh = 1, 0, 0, 0

    def row(self, title: str):
        self._y += self._rowh
        self._x, self._rowh = 0, 0
        self.d["panels"].append({"id": self._next(), "type": "row", "title": title, "collapsed": False, "gridPos": {"h": 1, "w": 24, "x": 0, "y": self._y}, "panels": []})
        self._y += 1

    def _next(self) -> int:
        self._id += 1
        return self._id

    def _place(self, w: int, h: int) -> dict:
        if self._x + w > 24:
            self._y += self._rowh
            self._x, self._rowh = 0, 0
        pos = {"h": h, "w": w, "x": self._x, "y": self._y}
        self._x += w
        self._rowh = max(self._rowh, h)
        return pos

    def stat(self, title, expr, unit="short", w=4, desc="", thresholds=None, decimals=None, zero=False):
        """zero=True shows 0 instead of "no data" for counts of events that simply have not happened yet."""
        expr = f"({expr}) or vector(0)" if zero else expr
        steps = thresholds or [{"color": "green", "value": None}]
        p = {"id": self._next(), "type": "stat", "title": title, "description": desc, "datasource": PROM, "gridPos": self._place(w, 4),
             "targets": [{"refId": "A", "expr": expr, "datasource": PROM, "instant": True}],
             "fieldConfig": {"defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}, **({"decimals": decimals} if decimals is not None else {}),
                                          "noValue": "no data yet"}, "overrides": []},
             "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value", "graphMode": "none", "textMode": "auto"}}
        self.d["panels"].append(p)

    def ts(self, title, series: list[tuple[str, str]], unit="short", w=8, h=7, desc="", stack=False):
        p = {"id": self._next(), "type": "timeseries", "title": title, "description": desc, "datasource": PROM, "gridPos": self._place(w, h),
             "targets": [{"refId": chr(65 + i), "expr": e, "legendFormat": legend, "datasource": PROM} for i, (e, legend) in enumerate(series)],
             "fieldConfig": {"defaults": {"unit": unit, "custom": {"lineWidth": 2, "fillOpacity": 15 if not stack else 40, "showPoints": "never",
                                                                   "stacking": {"mode": "normal" if stack else "none"}}}, "overrides": []},
             "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}}}
        self.d["panels"].append(p)

    def table(self, title, expr, w=12, h=8, desc=""):
        p = {"id": self._next(), "type": "table", "title": title, "description": desc, "datasource": PROM, "gridPos": self._place(w, h),
             "targets": [{"refId": "A", "expr": expr, "format": "table", "instant": True, "datasource": PROM}],
             "transformations": [{"id": "organize", "options": {"excludeByName": {"Time": True}, "renameByName": {"Value": "count"}}}],
             "options": {"showHeader": True, "sortBy": [{"displayName": "count", "desc": True}]}}
        self.d["panels"].append(p)

    def save(self):
        (OUT / f"{self.d['uid']}.json").write_text(json.dumps(self.d, indent=1))


def q(metric, p, by="le", win="15m"):
    return f"histogram_quantile({p}, sum by ({by}) (rate({metric}_bucket[{win}]))))".replace("))))", ")))")


OUT.mkdir(parents=True, exist_ok=True)
GREEN_RED = [{"color": "red", "value": None}, {"color": "orange", "value": 0.7}, {"color": "green", "value": 0.9}]
RED_GREEN = [{"color": "green", "value": None}, {"color": "orange", "value": 0.1}, {"color": "red", "value": 0.25}]

# =============================================================== 1. platform overview
b = Board("platform-overview", "Platform overview", "Health, workflow outcomes, end-to-end latency, approvals, HTTP and authentication.", ["overview"])
b.row("Health and outcomes")
b.stat("Services up", 'sum(up)', w=4, desc="Targets Prometheus can scrape right now.")
b.stat("Workflows started (1h)", 'sum(increase(workflow_transitions_total{status="STARTED"}[1h]))', w=4, zero=True)
b.stat("Completed (1h)", 'sum(increase(agent_workflow_runs_total{status="COMPLETED"}[1h]))', w=4, zero=True)
b.stat("Workflow success rate (1h)", '(sum(increase(agent_workflow_runs_total{status="COMPLETED"}[1h])) or vector(0)) / clamp_min(sum(increase(agent_workflow_runs_total[1h])), 1)', "percentunit", 4,
       "Completed / all runs that reached a final state.", GREEN_RED)
b.stat("Waiting for approval", 'sum(agent_workflows_waiting_for_approval)', w=4, desc="Runs paused until a manager decides.", zero=True)
b.stat("Events waiting for Kafka", 'sum(outbox_pending)', w=4, desc="Outbox rows not yet published. Should stay near zero.", thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 50}], zero=True)
b.ts("Workflow outcomes", [('sum by (status) (increase(agent_workflow_runs_total[5m]))', "{{status}}")], w=12, stack=True)
b.ts("Workflow transitions (workflow-service)", [('sum by (status) (increase(workflow_transitions_total[5m]))', "{{status}}")], w=12, desc="The system of record's view of state changes.")
b.row("Latency")
b.ts("End-to-end latency (request to final state, includes waiting for a human)", [(q("agent_workflow_end_to_end_seconds", 0.5, win="1h"), "p50"), (q("agent_workflow_end_to_end_seconds", 0.95, win="1h"), "p95")], "s", 8)
b.ts("Active latency (agents and code only)", [(q("agent_workflow_active_seconds", 0.5, win="1h"), "p50"), (q("agent_workflow_active_seconds", 0.95, win="1h"), "p95")], "s", 8)
b.ts("Approval time (request to decision)", [(q("agent_approval_wait_seconds", 0.5, win="1h"), "p50"), (q("agent_approval_wait_seconds", 0.95, win="1h"), "p95")], "s", 8)
b.row("Services")
b.ts("HTTP requests per second", [('sum by (application) (rate(http_server_requests_seconds_count[5m]))', "{{application}}")], "reqps", 8)
b.ts("5xx rate", [('sum by (application) (rate(http_server_requests_seconds_count{status=~"5.."}[5m]))', "{{application}}")], "reqps", 8)
b.ts("HTTP latency p95", [('histogram_quantile(0.95, sum by (le, application) (rate(http_server_requests_seconds_bucket[5m])))', "{{application}}")], "s", 8)
b.row("Authentication")
b.ts("Logins", [('sum by (result) (increase(auth_login_total[5m]))', "{{result}}")], w=8, stack=True)
b.ts("Rejected credentials by reason", [('sum by (reason) (increase(platform_auth_failures_total[5m]))', "{{reason}}")], w=8)
b.ts("JVM heap used", [('sum by (application) (jvm_memory_used_bytes{area="heap"})', "{{application}}")], "bytes", 8)
b.save()

# =============================================================== 2. agents and LLM
b = Board("agents-llm", "Agents and models", "Model latency, tokens and cost; tool calls; agent iterations; guardrails; retries.", ["agents", "llm"])
b.row("Cost and usage")
b.stat("Model spend (1h)", 'sum(increase(agent_llm_cost_usd_total[1h]))', "currencyUSD", 4, "Estimated from token counts and a price table.", decimals=4, zero=True)
b.stat("Tokens (1h)", 'sum(increase(agent_llm_tokens_total[1h]))', w=4, zero=True)
b.stat("Model calls (1h)", 'sum(increase(agent_llm_calls_total[1h]))', w=4, zero=True)
b.stat("Tool-call success rate (1h)", '(sum(increase(agent_tool_calls_total{result="ok"}[1h])) or vector(0)) / clamp_min(sum(increase(agent_tool_calls_total{result!="blocked"}[1h])), 1)', "percentunit", 4,
       "Calls that returned a result, out of calls that were allowed to run.", GREEN_RED)
b.stat("Guardrail rejections (1h)", 'sum(increase(agent_guardrail_rejections_total[1h]))', w=4, zero=True)
b.stat("Budget stops (1h)", 'sum(increase(agent_token_budget_exceeded_total[1h]))', w=4, thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}], zero=True)
b.ts("Tokens per minute by agent", [('sum by (agent) (rate(agent_llm_tokens_total[5m])) * 60', "{{agent}}")], w=12, stack=True)
b.ts("Spend by model (USD per hour)", [('sum by (model) (rate(agent_llm_cost_usd_total[15m])) * 3600', "{{model}}")], "currencyUSD", 12)
b.row("Model latency")
b.ts("Model latency p50 / p95 by agent", [(q("agent_llm_latency_seconds", 0.5, "le, agent"), "p50 {{agent}}"), (q("agent_llm_latency_seconds", 0.95, "le, agent"), "p95 {{agent}}")], "s", 12)
b.ts("Model calls per minute by agent", [('sum by (agent) (rate(agent_llm_calls_total[5m])) * 60', "{{agent}}")], w=12)
b.row("Agents and tools")
b.ts("Tool calls by result", [('sum by (result) (rate(agent_tool_calls_total[5m])) * 60', "{{result}}")], w=8, desc="blocked = refused by the tool guardrail before running.", stack=True)
b.ts("Tool latency p95", [(q("agent_tool_latency_seconds", 0.95, "le, tool"), "{{tool}}")], "s", 8)
b.ts("Agent iterations (model turns per run)", [('sum by (agent) (rate(agent_react_iterations_sum[1h])) / sum by (agent) (rate(agent_react_iterations_count[1h]))', "{{agent}}")], w=8)
b.ts("Graph step latency p95", [(q("agent_node_duration_seconds", 0.95, "le, node"), "{{node}}")], "s", 12)
b.ts("Graph step errors", [('sum by (node) (increase(agent_node_results_total{status="error"}[15m]))', "{{node}}")], w=12)
b.row("Guardrails and retries")
b.ts("Guardrail rejections by layer", [('sum by (layer) (increase(agent_guardrail_rejections_total[15m]))', "{{layer}}")], w=8, stack=True)
b.table("Top rejection codes (1h)", 'topk(12, sum by (layer, code) (increase(agent_guardrail_rejections_total[1h])))', w=8)
b.ts("Retries by kind", [('sum by (kind) (increase(agent_retries_total[15m]))', "{{kind}}")], w=8)
b.row("MCP servers")
b.ts("MCP tool calls by result", [('sum by (server, result) (rate(mcp_tool_calls_total[5m])) * 60', "{{server}} {{result}}")], w=8)
b.ts("MCP rejections by code", [('sum by (code) (increase(mcp_guardrail_rejections_total[15m]))', "{{code}}")], w=8)
b.ts("MCP tool latency p95", [(q("mcp_tool_latency_seconds", 0.95, "le, tool"), "{{tool}}")], "s", 8)
b.save()

# =============================================================== 3. RAG quality
b = Board("rag-quality", "RAG quality", "Retrieval relevance, reranker score, confidence, grounding, hallucination and correction rate, caches and latency.", ["rag"])
b.row("Answer quality")
b.stat("Answered from evidence (1h)", '1 - (sum(increase(rag_answers_total{outcome=~"insufficient|unavailable"}[1h])) or vector(0)) / clamp_min(sum(increase(rag_answers_total[1h])), 1)', "percentunit", 4, thresholds=GREEN_RED)
b.stat("Abstention rate (1h)", '(sum(increase(rag_answers_total{outcome="insufficient"}[1h])) or vector(0)) / clamp_min(sum(increase(rag_answers_total[1h])), 1)', "percentunit", 4, "Questions policy could not answer.")
b.stat("Hallucination / correction rate (1h)", '(sum(increase(rag_validation_total{result="rejected"}[1h])) or vector(0)) / clamp_min(sum(increase(rag_validation_total[1h])), 1)', "percentunit", 4,
       "Share of generated answers the grounding check rejected (they were corrected or replaced by verbatim evidence).", RED_GREEN)
b.stat("Verbatim fallback rate (1h)", '(sum(increase(rag_answers_total{outcome="extractive_fallback"}[1h])) or vector(0)) / clamp_min(sum(increase(rag_answers_total{outcome=~"grounded|extractive_fallback"}[1h])), 1)', "percentunit", 4)
b.stat("Answer cache hit rate (1h)", '(sum(increase(rag_answers_total{outcome="cache_hit"}[1h])) or vector(0)) / clamp_min(sum(increase(rag_answers_total[1h])), 1)', "percentunit", 4)
b.stat("Median confidence", q("rag_answer_confidence", 0.5, win="1h"), "percentunit", 4)
b.ts("Answers by outcome", [('sum by (outcome) (increase(rag_answers_total[10m]))', "{{outcome}}")], w=12, stack=True)
b.ts("Grounding validation issues by code", [('sum by (code) (increase(rag_validation_issues_total[15m]))', "{{code}}")], w=12, desc="What the validator caught: fabricated numbers, missing citations, contradictions...")
b.row("Relevance and confidence")
b.ts("Retrieval relevance (best retrieval confidence)", [(q("rag_retrieval_relevance", 0.1, win="1h"), "p10"), (q("rag_retrieval_relevance", 0.5, win="1h"), "p50"), (q("rag_retrieval_relevance", 0.9, win="1h"), "p90")], "percentunit", 8)
b.ts("Reranker score of the best cited passage", [(q("rag_reranker_top_score", 0.1, win="1h"), "p10"), (q("rag_reranker_top_score", 0.5, win="1h"), "p50"), (q("rag_reranker_top_score", 0.9, win="1h"), "p90")], "percentunit", 8)
b.ts("Answer confidence", [(q("rag_answer_confidence", 0.1, win="1h"), "p10"), (q("rag_answer_confidence", 0.5, win="1h"), "p50"), (q("rag_answer_confidence", 0.9, win="1h"), "p90")], "percentunit", 8)
b.row("Latency and caches")
b.ts("Pipeline stage latency p95", [(q("rag_stage_seconds", 0.95, "le, stage"), "{{stage}}")], "s", 8)
b.ts("Policy retrieval latency p95 (policy-service)", [(q("rag_retrieval_latency_seconds", 0.95, "le, cache"), "cache={{cache}}")], "s", 8)
b.ts("Embedding provider latency (avg)", [('rate(rag_embedding_provider_latency_seconds_sum[15m]) / rate(rag_embedding_provider_latency_seconds_count[15m])', "avg")], "s", 8)
b.ts("Embedding cache hit ratio", [('sum(rate(rag_embedding_cache_total{result="hit"}[30m])) / clamp_min(sum(rate(rag_embedding_cache_total[30m])), 0.001)', "hit ratio")], "percentunit", 8)
b.ts("Retrieval cache hit ratio", [('sum(rate(rag_retrieval_latency_seconds_count{cache="hit"}[30m])) / clamp_min(sum(rate(rag_retrieval_latency_seconds_count[30m])), 0.001)', "hit ratio")], "percentunit", 8)
b.row("Resilience")
b.ts("Degraded (keyword-only) retrievals", [('increase(rag_degraded_retrievals_total[15m])', "agent host saw degraded"), ('sum(increase(rag_retrieval_branch_failures_total[15m]))', "branch failures (policy-service)")], w=8)
b.ts("Abstentions at the policy service", [('increase(rag_retrieval_insufficient_total[15m])', "insufficient")], w=8)
b.ts("Policy documents ingested", [('increase(rag_ingest_documents_total[1h])', "documents")], w=8)
b.save()

# =============================================================== 4. business and safety
b = Board("business-safety", "Business and safety", "Refunds, approvals, compensation, notifications, rule rejections and security signals.", ["business", "security"])
b.row("Outcomes")
b.stat("Refunds initiated (24h)", 'sum(increase(refunds_initiated_total[24h]))', w=4, zero=True)
b.stat("Refunded amount (24h)", 'sum(increase(refunds_amount_sum[24h]))', "currencyUSD", 4, decimals=2, zero=True)
b.stat("Approved (24h)", 'sum(increase(approval_decisions_total{decision="APPROVED"}[24h]))', w=4, zero=True)
b.stat("Rejected (24h)", 'sum(increase(approval_decisions_total{decision="REJECTED"}[24h]))', w=4, zero=True)
b.stat("Expired (24h)", 'sum(increase(approval_decisions_total{decision="EXPIRED"}[24h]))', w=4, thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}], zero=True)
b.stat("Compensations (24h)", 'sum(increase(workflow_transitions_total{status="COMPENSATING"}[24h]))', w=4, desc="Replacement failed after stock was reserved; stock released.", thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}], zero=True)
b.ts("Approval decisions", [('sum by (decision) (increase(approval_decisions_total[15m]))', "{{decision}}")], w=8, stack=True)
b.ts("Approval decision time (avg)", [('sum(rate(approval_decision_time_seconds_sum[1h])) / clamp_min(sum(rate(approval_decision_time_seconds_count[1h])), 0.0001)', "avg")], "s", 8)
b.ts("Final workflow states", [('sum by (status) (increase(workflow_transitions_total{status=~"COMPLETED|PARTIALLY_COMPLETED|ESCALATED|REJECTED|FAILED"}[1h]))', "{{status}}")], w=8, stack=True)
b.row("Inventory and notifications")
b.ts("Inventory reservations", [('sum by (result) (increase(inventory_reservations_total[15m]))', "{{result}}")], w=12, stack=True, desc="out_of_stock = the atomic stock check refused; released = compensation.")
b.ts("Customer notifications by template", [('sum by (template) (increase(notifications_sent_total[1h]))', "{{template}}")], w=12)
b.row("Rules and security")
b.table("Business and authorization refusals by code (24h)", 'topk(15, sum by (code, status) (increase(platform_api_errors_total[24h])))', w=12, desc="Why services said no: ownership, approval, amounts, stock, internal-only...")
b.ts("Refusals by HTTP status", [('sum by (status) (increase(platform_api_errors_total[15m]))', "{{status}}")], w=12)
b.ts("Rejected credentials", [('sum by (reason) (increase(platform_auth_failures_total[15m]))', "{{reason}}")], w=8)
b.ts("Login throttling", [('sum by (result) (increase(auth_login_total[15m]))', "{{result}}")], w=8)
b.ts("Prompt-injection and input rejections", [('sum by (code) (increase(agent_guardrail_rejections_total{layer="input"}[15m]))', "{{code}}")], w=8)
b.save()
print("wrote", sorted(p.name for p in OUT.glob("*.json")))
