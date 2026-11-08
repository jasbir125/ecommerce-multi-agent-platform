# ecommerce-multi-agent-platform

An enterprise-grade Multi-Agent Ecommerce Order Recovery Platform. Demo workflow: **Delayed Shipment Recovery** — a customer reports a delayed order; agents investigate, retrieve policy via RAG, recommend a resolution, request human approval, and Spring Boot services execute the refund/replacement.

> **Core rule:** the model can suggest an action, but only authorized application code and domain services can execute it.

**Status: Phase 1 (skeleton).** Infrastructure, schema, topics and docs are real; service code is placeholder (health endpoints only).

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
Reset the database (re-runs `infra/postgres/init.sql`): `make clean && make infra`.

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
Phase 1 only exposes Actuator health. Once Phase 2+ lands:
```bash
curl http://localhost:8081/actuator/health
curl http://localhost:8092/health

# Start a recovery workflow (Phase 2)
curl -X POST http://localhost:8088/api/workflows -H 'Content-Type: application/json' \
  -d '{"customerId":"CUST-1001","orderId":"ORD-7842",
       "message":"My order ORD-7842 has been delayed by five days. Cancel the order, refund me, and suggest a cheaper replacement."}'

# Approve the refund
curl -X POST http://localhost:8085/api/approvals/<approvalId>/decision \
  -H 'Content-Type: application/json' -d '{"decision":"APPROVED","decidedBy":"manager-1"}'
```
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
- Local credentials (`ecommerce_local_pw`, `local-demo-token`, Grafana `admin/admin`) are for development only; Redis and Kafka run without auth/TLS locally.

## Demo instructions
1. `make up` and wait for containers to be healthy.
2. Open Streamlit (http://localhost:8501), enter customer `CUST-1001`, order `ORD-7842`, and submit the delayed-shipment request.
3. Watch agent progress, RAG evidence and the approval requirement; approve from the UI.
4. Check Kafka UI for events, Grafana for metrics.

Full 17-step script: `docs/demo-script.md` (filled in Phase 7). Scenarios and golden datasets: `evaluation/golden-datasets/`.

## Roadmap
1. **Phase 1** — monorepo, Compose, schema, topics, README ✅
2. Spring Boot services (Spring AI, REST, Kafka, DB, OpenTelemetry)
3. FastMCP ecommerce + policy servers
4. LangGraph agent host (supervisor + specialists)
5. Production RAG (pgvector, hybrid retrieval, reranking, caching, grounding, citations)
6. Streamlit UI
7. Guardrails, security, observability, evaluation datasets
8. AWS Bedrock deployment docs and configuration

## Model routing
`MODEL_PROVIDER` selects anthropic/openai/bedrock. Per-agent models are set via `MODEL_*` variables (all Sonnet to start; see `recommended_model`).
