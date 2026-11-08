# agent-host

FastAPI service that runs the LangGraph **supervisor + specialist agents** for delayed-shipment recovery.

**Core rule:** agents understand, plan, retrieve and recommend. They hold *read-only* tools. Anything that changes state (approval request, refund, reservation, replacement, notifications) is done by deterministic graph nodes through the MCP write tools, and only after a human approval has been verified.

## Graph
```
START -> input_guard -> supervisor -> [ order || shipment ] -> barrier -> [ policy || product || risk ]
      -> decision -> approval_request -> approval_wait (pauses) -> execute -> response -> END
         |                                  |
         +-> escalate -> response           +-> handle_no_approval (rejected/expired) -> response
```
| Node | Kind | Tools / behaviour |
|---|---|---|
| input_guard | code | length, ids, PII masking, bypass-approval / injection / destructive patterns |
| supervisor | LLM (structured output) | classifies intent, plans; unsupported intents go to a human |
| order, shipment | ReAct agents | `get_order_status`, `get_payment_status`, `get_shipment_status`; facts are read back from tool results |
| policy | ReAct agent + code | `search_policy` (RAG, citations); eligibility comes from `validate_refund_eligibility`, called by code with the verified facts |
| product | ReAct agent + code | `search_products`; the pick is checked against catalog results and the original price, with a rule-based fallback |
| risk | rules | no model: limits must be predictable |
| decision | LLM (structured output) | proposal only; validated by output guardrails, retried once, then a rule-based fallback; escalates without calling the model if facts are missing |
| approval_request / approval_wait | code | creates the request, then pauses (LangGraph `interrupt`). Status is always re-read from approval-service |
| execute | code | carrier case, refund, reserve, replacement; stable idempotency keys; compensation on failure |
| response | LLM + code | message is validated so it cannot claim anything a service did not return; fixed-template notifications carry the facts |

## Guardrails (`app/guardrails`)
1. **Input**: size, id format, PII masking, bypass-approval, prompt-injection, destructive commands, per-customer run rate limit, per-run token budget.
2. **Output**: action enum, refund <= order amount and hard limit, approval required, replacement must come from catalog results and cost no more, evidence required; customer text cannot claim an unverified refund/replacement or invent ids.
3. **Tool**: per-agent allow-list, write tools never given to a model, `order_id` must equal the workflow's order, call budget.
4. **Business/policy**: refunds need grounded policy evidence (confidence, citations, version) plus the deterministic eligibility result.

## Triggers
- `POST /api/agent/runs` (add `?stream=true` for server-sent events), `POST /api/agent/runs/{id}/resume`, `GET /api/agent/runs/{id}`, `GET /api/agent/runs/{id}/trace`. Send `X-Service-Token` and `X-Customer-Id`.
- Kafka: `workflow.started` starts a run; `workflow.approval.decided` resumes it. Event ids and per-workflow Redis locks prevent duplicate runs.

State is checkpointed in Postgres (`CHECKPOINTER=postgres`, falls back to memory). Each node writes a trace entry (start/end, status, retries, tool calls, tokens). Token use and estimated cost are tracked per agent; metrics go to `evaluation_result`.

## Models
`MODEL_PROVIDER=anthropic|openai`. Per-agent overrides (`MODEL_DECISION_AGENT`, ...) are honoured when the name fits the provider. Structured calls fall back to the other provider if its key is set.

## Develop
```bash
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -r agent-host/requirements-dev.txt     # use a separate venv from the MCP servers (see note)
cd agent-host && python -m pytest -q                   # 40 tests, no network or model needed
PYTHONPATH=. uvicorn app.main:app --reload --port 8092
python agent-host/scripts/e2e_demo.py                  # live scenario, needs the stack and a model key
python agent-host/scripts/e2e_demo.py --reject         # manager rejects: nothing is executed
```
*Note:* `fastmcp` (MCP servers) and `langchain-mcp-adapters` (this service) need different versions of the `mcp` package, so install them in separate virtualenvs. The Docker images are already separate.

## Not yet built
RAG internals (`app/rag`: query rewriting, reranking, caching, grounding validator) arrive in Phase 5; Streamlit in Phase 6; metrics dashboards and full guardrail test datasets in Phase 7.
