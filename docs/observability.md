# Observability

- **Tracing:** W3C `traceparent` flows across HTTP, MCP and Kafka (the outbox stores `trace_context`). View in Jaeger (http://localhost:16686); one recovery run is a single trace across all services.
- **Metrics:** every service exposes Prometheus metrics; Grafana (http://localhost:3000) has a provisioned platform-overview dashboard (`infra/grafana/dashboards`); build more on the same metrics.

- **Logs:** every service writes correlated, detailed logs (requests, responses, agent inputs/outputs, model and tool calls, Kafka messages). See [logging.md](logging.md) and use `make trace ID=<id>` to follow one request.
