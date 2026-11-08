# ecommerce-multi-agent-platform

An enterprise-grade Multi-Agent Ecommerce Order Recovery Platform. Demo workflow: **Delayed Shipment Recovery** — a customer reports a delayed order; agents investigate, retrieve policy via RAG, recommend a resolution, request human approval, and Spring Boot services execute the refund/replacement.

> **Core rule:** the model can suggest an action, but only authorized application code and domain services can execute it.

**Status: Phase 2 complete.** Infrastructure and all nine Spring Boot services are implemented and tested end to end. The Python MCP servers, agent host and Streamlit UI are still placeholders (Phases 3-6).

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
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r agent-host/requirements.txt -r mcp-servers/ecommerce-mcp-server/requirements.txt
cd agent-host && uvicorn app.main:app --reload --port 8092
cd mcp-servers/ecommerce-mcp-server && python -m app.main      # MCP on :8090/mcp
cd mcp-servers/policy-mcp-server && python -m app.main         # MCP on :8091/mcp
```

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
3. FastMCP ecommerce + policy servers
4. LangGraph agent host (supervisor + specialists)
5. Production RAG (pgvector, hybrid retrieval, reranking, caching, grounding, citations)
6. Streamlit UI
7. Guardrails, security, observability, evaluation datasets
8. AWS Bedrock deployment docs and configuration

## Phase 2 design notes
- **Common module** (`spring-boot-services/common`): service-token + caller filter, RFC 7807 errors, transactional outbox + relay, idempotent-consumer inbox, Kafka retry with exponential backoff and `.dlt` dead-letter topics, audit client, OpenTelemetry tracing (OTLP) and Prometheus metrics.
- **Policy RAG**: pgvector + full-text hybrid retrieval, reciprocal-rank fusion, rerank, version/date-aware, confidence threshold with an insufficient-evidence response. `POLICY_EMBEDDING_PROVIDER=openai` uses OpenAI embeddings; `hash` is an offline fallback. Changing provider requires re-ingesting policies.
- **Spring AI** (workflow-service): `MODEL_PROVIDER=anthropic|openai` selects the chat model; structured output (`IntentClassification`, `ResolutionDecision`), read-only tool calling and deterministic output guardrails.
- Not yet built: model fallback across providers, Redis caching and LLM-generated grounded answers (Phase 5).

## Model routing
`MODEL_PROVIDER` selects anthropic/openai/bedrock. Per-agent models are set via `MODEL_*` variables (all Sonnet to start; see `recommended_model`).
