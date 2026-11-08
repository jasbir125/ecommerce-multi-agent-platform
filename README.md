# ecommerce-multi-agent-platform

An enterprise-grade Multi-Agent Ecommerce Order Recovery Platform. Demo workflow: **Delayed Shipment Recovery** — a customer reports a delayed order; agents investigate, retrieve policy via RAG, recommend a resolution, request human approval, and Spring Boot services execute the refund/replacement.

> **Core rule:** the model can suggest an action, but only authorized application code and domain services can execute it.

**Status: Phase 5 complete.** Infrastructure, the nine Spring Boot services, the two FastMCP servers, the LangGraph agent host and a production-grade policy RAG are implemented and tested end to end. Only the Streamlit UI is still a placeholder (Phase 6).

## Layout

| Path | Contents |
|---|---|
| `spring-boot-services/` | Maven multi-module: order, shipment, payment, inventory, approval, policy, audit, workflow, notification |
| `agent-host/` | Python FastAPI + LangChain/LangGraph supervisor and specialist agents |
| `mcp-servers/` | Python FastMCP servers (ecommerce, policy) |
| `streamlit-ui/` | Internal demo / ops UI |
| `infra/` | Postgres `init.sql`, Kafka topics, OTel, Prometheus, Grafana, Redis, AWS docs |
| `evaluation/` | Golden datasets (14 scenarios), evaluators, reports |
| `docs/` | Architecture, API contracts, security, RAG, evaluation, demo script |

## Prerequisites
Docker Desktop (Compose v2), JDK 21, Maven 3.9+, Python 3.12+.

## Local setup
```bash
cp .env.example .env        # then add ANTHROPIC_API_KEY / OPENAI_API_KEY
make infra                  # Postgres, Kafka, Redis, OTel, Prometheus, Grafana
```
Reset the database (re-runs `infra/postgres/init.sql + seed.sql`): `make clean && make infra`.

## Docker commands
```bash
docker compose up -d                          # infrastructure only
docker compose --profile apps up -d --build   # + Spring, MCP, agent-host, Streamlit
docker compose ps
docker compose logs -f kafka-init             # topic creation output
docker compose down                           # add -v to wipe volumes
```
| UI | URL |
|---|---|
| Kafka UI | http://localhost:8180 |
| Grafana (admin/admin) | http://localhost:3000 |
| Prometheus | http://localhost:9090 |
| Streamlit | http://localhost:8501 |

Inside the network use `postgres:5432`, `kafka:19092`, `redis:6379`. From the host, Kafka is `localhost:9092`. Spring services run from an IDE are reachable from containers at `host.docker.internal:<port>`.

## Maven commands
```bash
cd spring-boot-services
mvn clean package -DskipTests                 # build everything
mvn -pl order-service -am spring-boot:run     # run one service
mvn test
```
Ports: order 8081, shipment 8082, payment 8083, inventory 8084, approval 8085, policy 8086, audit 8087, workflow 8088, notification 8089.

## Python commands
FastMCP needs Python 3.10+ (macOS ships 3.9), so use `uv` or pyenv:
```bash
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -r mcp-servers/ecommerce-mcp-server/requirements-dev.txt
make test-mcp                                                     # 36 unit tests
cd mcp-servers/ecommerce-mcp-server && PYTHONPATH=..:. python -m app.main   # MCP on :8090/mcp
cd mcp-servers/policy-mcp-server    && PYTHONPATH=..:. python -m app.main   # MCP on :8091/mcp
python mcp-servers/scripts/smoke_test.py                          # live end-to-end check (stack must be up)
# agent host (separate venv: it needs a different `mcp` version than fastmcp)
uv venv --python 3.12 .venv-agent && uv pip install --python .venv-agent/bin/python -r agent-host/requirements-dev.txt
cd agent-host && ../.venv-agent/bin/python -m pytest -q                       # 40 tests
python agent-host/scripts/e2e_demo.py                             # live scenario with real model calls
```
See `mcp-servers/README.md` for the tool list and security model.

## Streamlit commands
```bash
pip install -r streamlit-ui/requirements.txt
cd streamlit-ui && streamlit run app.py        # http://localhost:8501
```

## API testing examples
Every call needs `X-Service-Token` (value of `MCP_SERVICE_TOKEN`) and, for customer-scoped data, `X-Customer-Id`. Approvals need `X-Roles: MANAGER`; workflow resume needs `X-Roles: AGENT`.
```bash
H='-H X-Service-Token:local-demo-token -H X-Customer-Id:CUST-1001 -H Content-Type:application/json'

curl $H localhost:8081/api/orders/ORD-7842                       # own order
curl $H localhost:8081/api/orders/ORD-9001                       # 403: belongs to CUST-2002
curl $H localhost:8082/api/shipments/ORD-7842                    # LOST, 5 days
curl $H localhost:8086/api/policies/search -d '{"query":"Is my delayed order eligible for a refund?"}'
curl $H localhost:8086/api/policies/validate -d '{"orderId":"ORD-7842","orderAmount":129.99,"refundAmount":129.99,"delayDays":5,"shipmentStatus":"LOST"}'
curl $H "localhost:8084/api/catalog/products/search?category=headphones&maxPrice=129"

# start a workflow, request approval, approve, refund
curl $H localhost:8088/api/workflows -d '{"orderId":"ORD-7842","message":"My order ORD-7842 has been delayed by five days. Cancel the order, refund me, and suggest a cheaper replacement."}'
curl $H localhost:8085/api/approvals -d '{"workflowId":"<wf>","requestedAction":"REFUND","amount":129.99,"riskLevel":"LOW"}'
curl $H -H X-Roles:MANAGER localhost:8085/api/approvals/<approvalId>/decision -d '{"decision":"APPROVED","decidedBy":"manager-1"}'
curl $H localhost:8083/api/payments/refunds -d '{"workflowId":"<wf>","orderId":"ORD-7842","amount":129.99,"approvalId":"<approvalId>","idempotencyKey":"rf-1"}'
curl $H localhost:8088/api/workflows/<wf>/trace                   # state history + audit + Kafka events
curl $H localhost:8088/api/ai/classify -d '{"message":"my order is late"}'   # Spring AI structured output
```
Demo data: customers `CUST-1001`, `CUST-2002`; `ORD-7842` (shipped, lost, $129.99, owned by CUST-1001); replacement candidates `PRD-101`, `PRD-103`.

Inspect the data stores:
```bash
make psql        # then: \dt   and   \d policy_chunk
make topics
```

## Security notes
- `.env` is git-ignored; never commit real keys. `.env.example` holds placeholders only.
- LLMs never touch databases, SQL, arbitrary URLs or shells; they act only through MCP tools that call Spring Boot APIs.
- Write actions (refund, reserve inventory, replacement order, approval, carrier case) require workflow ID, ownership check, approval ID, idempotency key and limits — enforced in Spring services, not by the model.
- Retrieved documents, product text and customer messages are **data, not instructions** (prompt-injection defence).
- `workflow_audit` is append-only (a DB trigger blocks UPDATE/DELETE).
- Real keys live only in `.env`. Services authenticate each other with a shared service token, and customer ownership is checked in the Spring services on every read and write. Local credentials (`ecommerce_local_pw`, `local-demo-token`, Grafana `admin/admin`) are for development only; Redis and Kafka run without auth/TLS locally.

## Demo instructions
1. `make up` and wait for containers to be healthy.
2. Open Streamlit (http://localhost:8501), enter customer `CUST-1001`, order `ORD-7842`, and submit the delayed-shipment request.
3. Watch agent progress, RAG evidence and the approval requirement; approve from the UI.
4. Check Kafka UI for events, Grafana for metrics.

Full 17-step script: `docs/demo-script.md` (filled in Phase 7). Scenarios and golden datasets: `evaluation/golden-datasets/`.

## Roadmap
1. **Phase 1** — monorepo, Compose, schema, topics, README ✅
2. **Phase 2** — Spring Boot services (Spring AI, REST, Kafka, DB, OpenTelemetry) ✅
3. **Phase 3** — FastMCP ecommerce + policy servers ✅
4. **Phase 4** — LangGraph agent host (supervisor + specialists) ✅
5. **Phase 5** — Production RAG (pgvector, hybrid retrieval, reranking, caching, grounding, citations) ✅
6. Streamlit UI
7. Guardrails, security, observability, evaluation datasets
8. AWS Bedrock deployment docs and configuration

## Phase 2 design notes
- **Common module** (`spring-boot-services/common`): service-token + caller filter, RFC 7807 errors, transactional outbox + relay, idempotent-consumer inbox, Kafka retry with exponential backoff and `.dlt` dead-letter topics, audit client, OpenTelemetry tracing (OTLP) and Prometheus metrics.
- **Policy RAG**: pgvector + full-text hybrid retrieval, reciprocal-rank fusion, rerank, version/date-aware, confidence threshold with an insufficient-evidence response. `POLICY_EMBEDDING_PROVIDER=openai` uses OpenAI embeddings; `hash` is an offline fallback. Changing provider requires re-ingesting policies.
- **Spring AI** (workflow-service): `MODEL_PROVIDER=anthropic|openai` selects the chat model; structured output (`IntentClassification`, `ResolutionDecision`), read-only tool calling and deterministic output guardrails.
- Not yet built: model fallback across providers, Redis caching and LLM-generated grounded answers (Phase 5).

## Phase 3 design notes
- **Two FastMCP servers** call the Spring APIs only; they never touch the database. Shared code lives in `mcp-servers/mcp_common`.
- **Identity comes from headers** (`Authorization: Bearer`, `X-Customer-Id`, `X-Workflow-Id`), never from tool arguments, so a model cannot ask for another customer's data.
- **Write tools** are guarded (workflow id, idempotency key, amount/quantity limits, rate limit, audit) and only *request* actions; the Spring services still re-check everything.
- Catalog and policy text is scanned for prompt-injection phrases; flagged text is withheld and audited.

## Phase 4 design notes
- **Agents read, code acts.** LLM agents only get read-only MCP tools. Approval requests, refund, inventory, replacement and notifications are performed by deterministic nodes, after the approval status is re-read from approval-service.
- **Parallel where safe:** order and shipment run together, then policy, product and risk run together.
- **Human in the loop:** the graph pauses (`interrupt`) with state in Postgres; a manager's decision publishes `workflow.approval.decided`, which resumes the run.
- **Failure handling:** unverified shipment or weak policy evidence escalates without a refund; a failed replacement releases the reserved stock, keeps the refund and ends `PARTIALLY_COMPLETED`.
- Trigger by Kafka (`workflow.started`) or HTTP (`/api/agent/runs`, with SSE streaming). See `agent-host/README.md`.

## Phase 5 design notes (RAG)
- **Ingestion** is an async, incremental pipeline: extract, clean, semantic-chunk, reuse unchanged embeddings, batch-embed, store atomically, invalidate caches. Versioned, region/tenant/category-aware policies; archive without deleting.
- **Retrieval** is hybrid (pgvector HNSW + full-text with synonyms, rank fusion), reranked, region-aware, compressed to a token budget and cached in Redis. It degrades to keyword search if embeddings fail.
- **Answers** are generated from numbered evidence, then validated in code (citations, numbers, versions, polarity, injection). A failed answer is retried once, then replaced by verbatim evidence, then by an honest "could not verify".
- **Measured:** on the 13 held-out questions the full pipeline answered 10/10 correctly, abstained on 3/3 unanswerable, fabricated nothing; about 3 s and $0.0002 uncached, about 50 ms cached. See `docs/rag-optimization.md`, `docs/hallucination-controls.md`, `docs/evaluation.md`.
- `POST /api/agent/rag/ask` answers a policy question directly; the workflow's policy agent uses the same pipeline.
- After pulling this phase, reset the database (`make clean`): the policy schema changed.

## Model routing
`MODEL_PROVIDER` selects anthropic/openai (bedrock is Phase 8). Per-agent models are set via `MODEL_*` variables (all Sonnet to start; see `recommended_model`).
