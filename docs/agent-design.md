# Agent design standards

Every agent in `agent-host/app/agents/` follows the same layout and rules. Agents **retrieve, analyze and recommend**;
only the deterministic `execute` node (after a human approval) changes anything, and the business services stay the
source of truth for authorization and transactions.

## File layout (each `<agent>_agent.py`)

```
constants          AGENT_NAME, limits, SYSTEM_PROMPT
models             typed input / output (pydantic), Literal for constrained values
run()              orchestrates the steps below; returns the workflow update dict
validate_input()   required state present? else AgentInputError(code)
prepare_context()  what the model / rules are given
invoke_tools_or_agent()   the model or tool call (the only place that talks to them)
normalize_tool_results()  raw JSON -> typed, checked values
apply_business_rules()    deterministic validation (named predicates)
select_decision()         choose; model output is only accepted if verified
build_evidence()  /  build_result()   the response contract
handle_error()    coded error, no half-written state
```

`risk_agent` and `supervisor` use no tools, so their tool steps are documented placeholders.

## Response contract (unchanged)

Each agent returns `{ "<output field>": ..., "errors": [...], "evidence": [...] }` (plus the private trace keys
`_detail`, `_retries`, `_tool_errors`). It is built from a typed `AgentUpdate` subclass (`agents/base.py`); fields
that are `None` are not written, so a failed agent never overwrites existing state.

| Agent | Output field |
|---|---|
| product | `product_recommendation` |
| order | `order` |
| shipment | `shipment` |
| policy | `policy` |
| risk | `risk` |
| decision | `resolution` |
| response | `customer_response` |
| supervisor | `intent`, `plan` |

## Rules applied everywhere

- **Money** is `Decimal` rounded half-up to cents (`models/money.py`, type `Money`). It is a plain JSON number on the wire, and
  becomes a float only at the one boundary function `money_as_number`. Amounts are never compared across currencies.
- **Errors**: only expected exception tuples are caught (`MODEL_ERRORS` in `llm.py`, `TOOL_ERRORS` / `TOOL_FAILURES` in
  `tool_guardrails.py`). A spent token budget (`BudgetExceeded`) and programming errors propagate. The workflow errors
  list gets `CODE: ExceptionType` (or the service's own code); the full exception is in the log.
- **Logging**: one `AGENT_DONE` line per agent (`agent_name`, `outcome`, `latency_ms`, `error_codes`, `workflow_id`,
  `request_id`, `customer_id`, `order_id`) and an `AGENT_ERROR` line per failure with `tool_name`. Exception text is masked
  and truncated by `logctx.clean`. No prompts are logged here. Metric: `agent_runs_total{agent,outcome}`.
- **Trust**: model output, retrieved policy text, product text and customer messages are data. A model-chosen id must be in
  the verified tool results; prices, stock, eligibility, approval status and policy versions come only from tools.
- **Determinism**: temperature 0 for every agent except `response` (0.3), whose text is still checked against verified facts.

## Behavior changes made by the refactor

1. **Product fallback picks the lowest-priced valid candidate** (was: the highest price at or below the original). This is what the
   written business rule says. It only matters when the model's pick is missing or invalid.
2. **Currency check**: a replacement whose currency differs from the order's is not a candidate. Candidates with a non-positive price are dropped.
3. **Rounding**: the per-unit budget is rounded half-up (was Python's round-half-even) and comparisons are exact in cents (was a 1e-9 float tolerance).
4. **Malformed catalog rows** are skipped and reported as `PRODUCT_CANDIDATE_MALFORMED` (before, one bad row raised and failed the step).
5. **Order agent**: a tool result for a different order id, a missing required field, or a non-positive amount is `ORDER_UNVERIFIED`.
6. **Shipment agent**: a result for another order, negative delay days, or an exception type other than NONE / DELAYED / LOST is treated as unverified.
7. **Error text**: model failures are recorded as `..._AGENT_ERROR: <ExceptionType>` instead of the full exception message (the message is in the log).
8. **Unexpected exceptions** (bugs) now fail the step instead of being converted into a note. Expected provider, network, timeout and tool errors are still handled.
9. **Policy agent**: a malformed `ask_policy` result falls back to direct retrieval and adds `POLICY_RESULT_MALFORMED`.
10. **Response agent** returns the workflow update (`customer_response`, `errors`, `evidence`) instead of a `(text, notes)` tuple; `response_node` was adapted.
11. **Temperature** of the response agent is 0.3 (was 0).
12. New optional field `currency` on `ProductCandidate`; money fields on `OrderSnapshot`, `ProductCandidate`, `ProductRecommendation` and `ResolutionDecision` are `Decimal` in Python (JSON output unchanged).
13. Metric added: `agent_runs_total`.
14. **"Cheaper" means strictly cheaper**: a replacement must cost less than the original unit price (was: not more). The output guardrail `REPLACEMENT_COSTS_MORE` enforces the same; `refund-policy-v2` now says "cheaper replacement".

## Not changed (and why)

- Top-level process loops (`runner.py`, `kafka_consumer.py`, `main.py`, API route error mapping, tracing, evaluator, log masking) still use a
  catch-all that logs and continues: they are process boundaries that must not die on one bad message.
- Timeouts, rate-limit retries, circuit breaker and idempotency keys for writes already existed in `McpGateway`, `PolicyRetriever` and the `execute` node; they were not duplicated in the agents.
