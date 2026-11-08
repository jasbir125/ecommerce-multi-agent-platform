# Architecture

**Core rule:** the model can suggest an action, but only authorized application code and domain services can execute it.

| Layer | Role |
|---|---|
| LLM agents (LangGraph) | Understand, plan, retrieve, recommend, summarize |
| MCP (FastMCP) | Standardized, controlled access to tools and knowledge |
| Spring Boot services | Validate, authorize, execute, persist, publish events |
| Kafka | Durable event-driven communication |
| Streamlit | Internal demo and operations UI |
| AWS Bedrock | Managed production option |

## Ports

| Component | Port |
|---|---|
| order / shipment / payment / inventory | 8081 / 8082 / 8083 / 8084 |
| approval / policy / audit / workflow / notification | 8085 / 8086 / 8087 / 8088 / 8089 |
| ecommerce-mcp / policy-mcp / agent-host | 8090 / 8091 / 8092 |
| Streamlit / Kafka UI | 8501 / 8180 |
| Postgres / Redis / Kafka | 5432 / 6379 / 9092 |
| Prometheus / Grafana / OTLP | 9090 / 3000 / 4317 |

_Detailed diagrams are added in later phases._
