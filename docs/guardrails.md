# Guardrails

Each guardrail below maps to where it is implemented and what tests it.

| Guardrail | Implementation | Test |
|---|---|---|
| Input validation / prompt injection | `agent-host/app/guardrails/input_guardrails.py` | `tests/test_guardrails.py`, golden `prompt-injection` |
| Tool allow-list and argument checks | `guardrails/tool_guardrails.py`, MCP server write guards | `test_guardrails.py`, golden `unauthorized-access`, mode `mcp` |
| Policy citation and version check | `guardrails/policy_guardrails.py` (`POLICY_VERSION_MISMATCH`) | `test_guardrails.py`, golden `incorrect-policy-retrieval` |
| Output grounding (dates, refund status, approval claims, delivery promises) | `guardrails/output_guardrails.py` | `test_guardrails.py`, golden `hallucinated-*` |
| RAG grounding and insufficient-evidence refusal | `rag/pipeline.py` grounding validator, extractive fallback | `test_rag_pipeline.py`, golden `insufficient-rag-evidence` |
| Human approval before money moves | deterministic write nodes re-read approval status; LangGraph `interrupt` | `test_graph_flow.py`, golden `approval-rejection` |
| Refund cap | payment-service and MCP guard | golden `refund-exceeds-order-amount` |
| Idempotency / duplicate events | outbox + inbox, request ids | golden `duplicate-event` |
| Fault tolerance | partial completion, escalation | golden `partial-failure`, `missing-shipment-data` |
| Invalid model output | schema validation and retry, safe fallback | golden `invalid-model-output`, `low-confidence-decision` |

Golden scenarios are checked by `evaluation/run_golden.py` (`make eval`); the unit tests in `agent-host/tests` cover the guardrails directly.
