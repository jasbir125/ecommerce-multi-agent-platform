# Logging and tracing a request

Every service writes detailed, correlated logs, so one customer request can be followed from the screen, through every service,
agent, model call and tool call, to the final answer, using a single id.

## The ids

| Id | What it is | Example |
|---|---|---|
| **corr** (correlation id) | One per customer action. Created by the UI (or the first caller) and sent as the `X-Correlation-Id` header. Every service keeps it, every Kafka event carries it, every agent and tool call logs it. After a manager approves, the resumed case keeps the **original** request's id for its downstream calls. | `trace-demo-1794160147` |
| **wf** (workflow id) | The case. Appears in every line that belongs to the case, including the manager's approval click (which has its own corr). | `WF-943C8A713B` |
| **trace** | OpenTelemetry trace id (the same id Jaeger shows) for one hop chain. | `299db904bfc2f6c8d8e5b5a5b8070b72` |
| **agent** | The graph step that is running (`supervisor`, `order`, `shipment`, `policy`, `decision`, `execute`...). | `shipment` |

The UI shows the tracking id after you submit a request ("Tracking id for support / debugging"). The id is also returned in the
`X-Correlation-Id` response header of every API call.

## Follow one request

```bash
make trace ID=<correlation id | workflow id | order id>          # last 3 hours
make trace ID=WF-943C8A713B SINCE=30m
python3 scripts/trace_request.py WF-943C8A713B --service agent   # only the agent host
python3 scripts/trace_request.py <id> --full                      # do not shorten long lines
```

It reads the logs of all containers, keeps the lines of that case (the workflow id and every correlation id used on it, so the
manager's approval click is included), sorts them by time and prints one timeline. Real output (shortened):

```
17:49:08.251  workflow-service         INFO   http.access: -> POST /api/workflows  from=192.168.65.1 corr=trace-demo-1794160147 wf=-
17:49:08.737  workflow-service         INFO   http.access: <- POST /api/workflows  201 478ms caller=customer=CUST-1001[CUSTOMER] corr=trace-demo-1794160147 wf=WF-943C8A713B  req={"orderId":"ORD-PM231907195","message":"My order ORD...
17:49:09.168  agent-host               INFO   agent.kafka: KAFKA IN  topic=workflow.started type=WorkflowStarted eventId=ad620430-354c-48fc-b980-1683844f9db9 corr=trace-demo-1794160147 wf=WF-943C8A713B cause=None payload={"orderId":...
17:49:13.083  agent-host               INFO   [shipment] agent.graph: AGENT > shipment received input={"workflow_id": "WF-943C8A713B", "customer_id": "CUST-1001", "order_id": "ORD-PM231907195", "customer_message": "My order ORD-PM23...
17:49:13.397  agent-host               INFO   [shipment] agent.llm: LLM > shipment model=gpt-4o-mini messages=2 chars=577 system=You are the Shipment Agent. Check the carrier tracking status for the customer's order. Rules you must ...
17:49:14.367  agent-host               INFO   [shipment] agent.llm: LLM < shipment model=gpt-4o-mini 970ms tokens in=197 out=22 tool_calls=['get_shipment_status({"order_id": "ORD-PM231907195"})'] answer=-
17:49:14.368  agent-host               INFO   [shipment] agent.tool: TOOL > get_shipment_status (called by the shipment agent) args={"order_id": "ORD-PM231907195"}
17:49:14.381  ecommerce-mcp            INFO   mcp.tool: TOOL IN  get_shipment_status args={"order_id": "ORD-PM231907195"}
17:49:14.481  shipment-service         INFO   http.access: <- GET /api/shipments/ORD-PM231907195  200 79ms caller=service(customer=CUST-1001) corr=trace-demo-1794160147 wf=WF-943C8A713B  req=-  resp={"orderId":"ORD-PM231907195","cus...
17:49:14.485  ecommerce-mcp            INFO   mcp.tool: TOOL OUT get_shipment_status ok 104ms result={"orderId": "ORD-PM231907195", "carrier": "BlueDart", "trackingNo": "BD-ORD-PM231907195", "status": "LOST", "expectedDelivery": "20...
17:49:15.662  agent-host               INFO   [shipment] agent.graph: AGENT < shipment ok 2578ms tools=['get_shipment_status'] tokens=601 retries=0 detail=The shipment status for order ORD-PM231907195 indicates that it is lost. The ...
17:49:26.503  agent-host               INFO   [decision] agent.graph: AGENT < decision ok 2039ms tools=[] tokens=2252 retries=0 detail=The shipment is confirmed lost and the customer is eligible for a full refund or a replacement. o...
17:49:26.706  workflow-service         INFO   com.example.workflow.WorkflowService: WORKFLOW WF-943C8A713B INVESTIGATING -> WAITING_FOR_APPROVAL reason=approval APR-81318B36ED requested key=agent-waiting-approval
17:49:28.051  approval-service         INFO   http.access: -> POST /api/approvals/APR-81318B36ED/decision  from=192.168.65.1 corr=approve-click-1 wf=-
17:49:28.140  agent-host               INFO   agent.runner: RUN resume wf=WF-943C8A713B customer=CUST-1001 order=ORD-PM231907195 status=WAITING_FOR_APPROVAL origin_corr=trace-demo-1794160147 trigger_corr=approve-click-1
17:49:29.040  agent-host               INFO   [execute] agent.tool: TOOL > initiate_refund (by graph step execute) args={"workflow_id": "WF-943C8A713B", "order_id": "ORD-PM231907195", "amount": 129.99, "approval_id": "APR-81318B36ED...
17:49:32.195  agent-host               INFO   agent.runner: RUN finished wf=WF-943C8A713B status=COMPLETED intent=DELAYED_ORDER_RECOVERY approval=APR-81318B36ED errors=[] tokens=8233 cost_usd=0.001545 response=Thank you for your ord...
```

How to read it: `->` is a request arriving at a service, `<-` is the answer (status, time, caller, request body, response body),
`=>` is a call a service makes to another one, `KAFKA IN/OUT` and `EVENT queued` are messages, `AGENT >/<` is what an agent
step received and returned, `LLM >/<` is the model call (prompt in, answer and token counts out), `TOOL >/<` is a tool call
(arguments in, result out), `TOOL IN/OUT` is the same call seen from the MCP server, `WORKFLOW ... -> ...` is a state change,
and `RUN start/resume/finished` marks the lifecycle of the AI run.

## What is logged where

| Layer | Logger | What you get |
|---|---|---|
| Every Spring service | `http.access` | `->` and `<-` for each API call: method, path, query, status, duration, caller (user or service, roles), corr, wf, request body, response body |
| Spring service-to-service | `http.client` | `=>` outgoing call: target, status, duration, request body |
| Spring events | `EventPublisher`, `OutboxRelay`, `EventLog` | `EVENT queued` (what was decided), `KAFKA OUT` (topic/partition/offset), `KAFKA IN` (consumer, ids, payload, duplicates) |
| workflow-service | `WorkflowService` | every state change `WORKFLOW wf A -> B reason=...`, and refused moves |
| agent-host HTTP | `http.access` | same `->` / `<-` lines (streaming answers are logged as `<stream, N chunks>`) |
| agent-host runs | `agent.runner`, `agent.kafka` | `RUN start/resume/paused/finished` with status, errors, tokens, cost, customer reply; `KAFKA IN` with payload |
| each agent step | `agent.graph` | `AGENT >` (the case data the step received) and `AGENT <` (status, time, tools used, tokens, output) |
| model calls | `agent.llm` | `LLM >` (model, message count, system line, latest input) and `LLM <` (time, tokens, tool calls, answer) |
| tool calls | `agent.tool` | `TOOL >` arguments, `TOOL <` result or error, `BLOCKED by guardrail` when a rule refuses it |
| policy search | `rag.pipeline` | `RAG >` question and filters, `RAG <` confidence, grounded or not, sources with versions, dropped passages (e.g. suspected injection), timings, answer |
| MCP servers | `mcp.tool`, `mcp.spring` | `TOOL IN/OUT` with caller ids, and `=>` for the call to the Spring service |
| UI | `ui.api` | each call the screen makes, with its correlation id |

## Safety of the logs

Bodies are **masked and truncated** before they are written: passwords, tokens and keys (by field name), JWTs, `Bearer` values,
API keys, e-mail addresses and phone numbers are replaced (`"password":"***"`, `[email]`, `[phone]`). `Authorization` and
`X-Service-Token` headers are never logged. Business values (order ids, amounts, statuses, policy text) are kept because debugging needs
them. A check on a full session of logs found no tokens, keys, e-mails or passwords.

Customer free text is still logged (masked for e-mail/phone). If your data rules forbid that, turn the content off:

| Setting (environment) | Default | Effect |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Python services and MCP servers |
| `LOG_FORMAT` | `text` | `json` writes one JSON object per line (use it on AWS/CloudWatch) |
| `LOG_PAYLOAD_CHARS` | `1500` | maximum characters of any logged body, argument or result |
| `LOG_LLM_CONTENT` | `true` | `false` logs sizes and tool names of model calls but not prompts or answers |
| `PLATFORM_LOGGING_BODIES` | `true` | Spring services: `false` logs the `->`/`<-` lines without bodies |
| `PLATFORM_LOGGING_MAX_BODY_CHARS` | `2000` | Spring services: maximum characters of a logged body |

Health and metrics endpoints are not logged (they would drown the useful lines). Log volume is roughly 300-350 lines for one
complete recovery; plan retention accordingly (the Terraform sets 30 days).

## On AWS

Each ECS service writes to its own CloudWatch log group (`/ecs/<name>/<service>`), and the Python services log JSON. In CloudWatch Logs
Insights, select all the groups and run:

```
fields @timestamp, @log, @message
| filter @message like /WF-943C8A713B/ or @message like /trace-demo-1794160147/
| sort @timestamp asc
```

## When something goes wrong: a quick recipe

1. Get the id: the tracking id on the screen, the `X-Correlation-Id` header of the failing call, or the workflow id.
2. `make trace ID=<id>`.
3. Look for `WARN`/`ERROR`, `FAILED`, `BLOCKED by guardrail`, `REFUSED`, status `4xx/5xx` on a `<-` line, or `refused move`.
4. Read the line just before it: the `AGENT >` input, the `TOOL >` arguments or the `->` request body show what was received; the matching `<`
   line shows what was returned.
5. For a timing problem compare the `ms` values of the `<-`, `LLM <` and `TOOL <` lines.
