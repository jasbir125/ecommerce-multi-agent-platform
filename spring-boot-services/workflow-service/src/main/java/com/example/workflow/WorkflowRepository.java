package com.example.workflow;

import com.example.workflow.WorkflowRecords.Workflow;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

@Repository
public class WorkflowRepository {

    private static final String COLS = "workflow_id, request_id, customer_id, order_id, status, state_json::text AS state_json, version, created_at, updated_at";

    private final JdbcClient jdbc;
    private final ObjectMapper mapper;

    public WorkflowRepository(JdbcClient jdbc, ObjectMapper mapper) {
        this.jdbc = jdbc;
        this.mapper = mapper;
    }

    /** @return true if inserted, false if the requestId already exists (idempotent create). */
    public boolean insert(String id, String requestId, String customerId, String orderId, String status, Map<String, Object> state) {
        return jdbc.sql("""
                INSERT INTO workflow_instance (workflow_id, request_id, customer_id, order_id, status, state_json)
                VALUES (:id, :req, :cust, :ord, :st, CAST(:state AS jsonb)) ON CONFLICT (request_id) DO NOTHING""")
                .param("id", id).param("req", requestId).param("cust", customerId).param("ord", orderId)
                .param("st", status).param("state", json(state)).update() == 1;
    }

    public Optional<Workflow> findById(String id) {
        return jdbc.sql("SELECT " + COLS + " FROM workflow_instance WHERE workflow_id = :id")
                .param("id", id).query((rs, i) -> map(rs)).optional();
    }

    public Optional<Workflow> findByRequestId(String requestId) {
        return jdbc.sql("SELECT " + COLS + " FROM workflow_instance WHERE request_id = :r")
                .param("r", requestId).query((rs, i) -> map(rs)).optional();
    }

    public List<Workflow> list(String status) {
        return jdbc.sql("SELECT " + COLS + " FROM workflow_instance WHERE (CAST(:s AS text) IS NULL OR status = :s) ORDER BY created_at DESC LIMIT 100")
                .param("s", status == null || status.isBlank() ? null : status.toUpperCase())
                .query((rs, i) -> map(rs)).list();
    }

    /** Optimistic concurrency: only updates if the stored version matches. */
    public boolean update(String id, long expectedVersion, String status, Map<String, Object> state) {
        return jdbc.sql("""
                UPDATE workflow_instance SET status = :st, state_json = CAST(:state AS jsonb),
                       version = version + 1, updated_at = now()
                WHERE workflow_id = :id AND version = :v""")
                .param("st", status).param("state", json(state)).param("id", id).param("v", expectedVersion).update() == 1;
    }

    /** Events this workflow published (read from the outbox), for the trace / Kafka panel. */
    public List<Map<String, Object>> eventsFor(String workflowId) {
        return jdbc.sql("""
                SELECT topic, event_id, (payload::jsonb)->>'type' AS type,
                       (payload::jsonb)->>'correlationId' AS correlation_id,
                       (payload::jsonb)->>'causationId' AS causation_id, (payload::jsonb)->>'workflowId' AS workflow_id,
                       (payload::jsonb)->'payload' AS body, created_at, sent_at
                FROM outbox_event WHERE (payload::jsonb)->>'workflowId' = :w ORDER BY id""")
                .param("w", workflowId).query().listOfRows().stream().map(this::withParsedBody).toList();
    }

    /** Most recent events across all workflows (operator feed). */
    public List<Map<String, Object>> recentEvents(int limit, String topic) {
        return jdbc.sql("""
                SELECT topic, event_id, (payload::jsonb)->>'type' AS type,
                       (payload::jsonb)->>'correlationId' AS correlation_id,
                       (payload::jsonb)->>'causationId' AS causation_id, (payload::jsonb)->>'workflowId' AS workflow_id,
                       (payload::jsonb)->'payload' AS body, created_at, sent_at
                FROM outbox_event WHERE (CAST(:t AS text) IS NULL OR topic = CAST(:t AS text))
                ORDER BY id DESC LIMIT :n""")
                .param("t", topic == null || topic.isBlank() ? null : topic).param("n", Math.max(1, Math.min(limit, 200)))
                .query().listOfRows().stream().map(this::withParsedBody).toList();
    }

    private Map<String, Object> withParsedBody(Map<String, Object> row) {
        Map<String, Object> out = new java.util.LinkedHashMap<>(row);
        Object body = row.get("body");
        try {
            out.put("payload", body == null ? Map.of() : mapper.readValue(body.toString(), new TypeReference<Map<String, Object>>() { }));
        } catch (JsonProcessingException e) {
            out.put("payload", Map.of());
        }
        out.remove("body");
        return out;
    }

    private Workflow map(java.sql.ResultSet rs) throws java.sql.SQLException {
        try {
            Map<String, Object> state = mapper.readValue(rs.getString("state_json"), new TypeReference<>() { });
            return new Workflow(rs.getString("workflow_id"), rs.getString("request_id"), rs.getString("customer_id"),
                    rs.getString("order_id"), rs.getString("status"), state, rs.getLong("version"),
                    rs.getTimestamp("created_at").toLocalDateTime(), rs.getTimestamp("updated_at").toLocalDateTime());
        } catch (JsonProcessingException e) {
            throw new java.sql.SQLException("Corrupt workflow state", e);
        }
    }

    private String json(Map<String, Object> m) {
        try {
            return mapper.writeValueAsString(m);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException(e);
        }
    }
}
