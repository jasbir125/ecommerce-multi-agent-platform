"""Prometheus metrics for the agent host, named after what the spec asks to track.

end-to-end latency ........ agent_workflow_end_to_end_seconds (request -> final state, incl. human approval) and
                            agent_workflow_active_seconds (agents/code only)
model latency / tokens / cost  agent_llm_latency_seconds, agent_llm_tokens_total, agent_llm_cost_usd_total
tool-call success rate .... agent_tool_calls_total{result}
retrieval relevance / reranker score / RAG confidence  rag_retrieval_relevance, rag_reranker_top_score, rag_answer_confidence
agent iterations .......... agent_react_iterations
approval time ............. agent_approval_wait_seconds
retry count ............... agent_retries_total
workflow success rate ..... agent_workflow_runs_total{status}
hallucination / correction  rag_validation_total{result}, rag_validation_issues_total{code}, rag_answers_total{outcome}
"""
from prometheus_client import Counter, Gauge, Histogram

LATENCY = (.05, .1, .25, .5, 1, 2, 3, 5, 8, 13, 21, 34, 60)
LONG = (1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 1800, 3600)
SCORE = (.1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0)

WORKFLOW_RUNS = Counter("agent_workflow_runs", "Workflow runs that reached a final state", ["status"])
WORKFLOW_E2E = Histogram("agent_workflow_end_to_end_seconds", "Request to final state, including time waiting for a human", ["status"], buckets=LONG)
WORKFLOW_ACTIVE = Histogram("agent_workflow_active_seconds", "Time agents and code were running (waiting for a human excluded)", ["status"], buckets=LATENCY)
WORKFLOWS_WAITING = Gauge("agent_workflows_waiting_for_approval", "Runs currently paused for a human approval")
APPROVAL_WAIT = Histogram("agent_approval_wait_seconds", "Time from approval request to decision", ["decision"], buckets=(5, 15, 30, 60, 120, 300, 600, 1200, 1800))

NODE_DURATION = Histogram("agent_node_duration_seconds", "Duration of a graph step", ["node"], buckets=LATENCY)
NODE_RESULTS = Counter("agent_node_results", "Graph step outcomes", ["node", "status"])
REACT_ITERATIONS = Histogram("agent_react_iterations", "Model turns a specialist agent needed", ["agent"], buckets=(1, 2, 3, 4, 5, 6, 8, 10, 14))

LLM_CALLS = Counter("agent_llm_calls", "Model calls", ["agent", "model"])
LLM_LATENCY = Histogram("agent_llm_latency_seconds", "Model call duration", ["agent", "model"], buckets=LATENCY)
LLM_TOKENS = Counter("agent_llm_tokens", "Tokens used", ["agent", "model", "kind"])
LLM_COST = Counter("agent_llm_cost_usd", "Estimated model cost in USD", ["agent", "model"])
BUDGET_EXCEEDED = Counter("agent_token_budget_exceeded", "Runs stopped by the per-run token budget")

TOOL_CALLS = Counter("agent_tool_calls", "MCP tool calls made by agents and graph nodes", ["agent", "tool", "result"])
TOOL_LATENCY = Histogram("agent_tool_latency_seconds", "Tool call duration", ["tool"], buckets=LATENCY)

GUARDRAIL = Counter("agent_guardrail_rejections", "Guardrail rejections by layer (input, output, tool, business, rag) and code", ["layer", "code"])
RETRIES = Counter("agent_retries", "Retries by kind (decision, rate_limit, model_fallback)", ["kind"])

RAG_ANSWERS = Counter("rag_answers", "RAG answers by outcome", ["outcome"])
RAG_CONFIDENCE = Histogram("rag_answer_confidence", "Confidence of answered RAG questions", buckets=SCORE)
RAG_RELEVANCE = Histogram("rag_retrieval_relevance", "Best retrieval confidence per question", buckets=SCORE)
RAG_RERANK_TOP = Histogram("rag_reranker_top_score", "Reranker score of the best cited passage", buckets=SCORE)
RAG_VALIDATION = Counter("rag_validation", "Grounding validation of generated answers", ["result"])
RAG_VALIDATION_ISSUES = Counter("rag_validation_issues", "Grounding issues found, by code", ["code"])
RAG_STAGE = Histogram("rag_stage_seconds", "RAG pipeline stage duration", ["stage"], buckets=LATENCY)
RAG_DEGRADED = Counter("rag_degraded_retrievals", "Retrievals served in degraded (keyword-only) mode")


def guardrail(layer: str, code: str) -> None:
    GUARDRAIL.labels(layer, code.split(":")[0][:60]).inc()
