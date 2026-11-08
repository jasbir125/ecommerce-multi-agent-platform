package com.example.audit;

import java.util.Map;
import java.time.LocalDateTime;
import jakarta.validation.constraints.NotBlank;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.List;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;

/** Append-only: there is deliberately no update or delete method (and a DB trigger enforces it). */
@Service
public class AuditService {

    private final JdbcClient jdbc;
    private final ObjectMapper mapper;

    public AuditService(JdbcClient jdbc, ObjectMapper mapper) {
        this.jdbc = jdbc;
        this.mapper = mapper;
    }

    public long append(AuditEventRequest req) {
        String payload;
        try {
            payload = mask(mapper.writeValueAsString(req.payload() == null ? java.util.Map.of() : req.payload()));
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException("payload is not serializable", e);
        }
        return jdbc.sql("""
                INSERT INTO workflow_audit (workflow_id, agent_name, event_type, payload, actor, correlation_id, causation_id)
                VALUES (:wf, :agent, :type, CAST(:payload AS jsonb), :actor, :corr, :cause) RETURNING id""")
                .param("wf", req.workflowId() == null ? "N/A" : req.workflowId())
                .param("agent", req.agentName()).param("type", req.eventType()).param("payload", payload)
                .param("actor", mask(req.actor())).param("corr", req.correlationId()).param("cause", req.causationId())
                .query(Long.class).single();
    }

    public List<AuditEvent> forWorkflow(String workflowId) {
        return jdbc.sql("""
                SELECT id, workflow_id, agent_name, event_type, payload::text AS payload, actor,
                       correlation_id, causation_id, created_at
                FROM workflow_audit WHERE workflow_id = :wf ORDER BY id""")
                .param("wf", workflowId).query(AuditEvent.class).list();
    }

    // ---- masks emails, phone numbers and card-like numbers before anything is stored
    private static final java.util.regex.Pattern EMAIL = java.util.regex.Pattern.compile("([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\\.[A-Za-z]{2,})");
    private static final java.util.regex.Pattern CARD = java.util.regex.Pattern.compile("\\b(?:\\d[ -]?){13,16}\\b");
    private static final java.util.regex.Pattern PHONE = java.util.regex.Pattern.compile("(?<!\\d)\\+?\\d[\\d -]{8,}\\d(?!\\d)");

    static String mask(String text) {
        if (text == null) {
            return null;
        }
        String out = EMAIL.matcher(text).replaceAll("$1***@$2");
        out = CARD.matcher(out).replaceAll("[CARD-REDACTED]");
        return PHONE.matcher(out).replaceAll("[PHONE-REDACTED]");
    }

    // ---- DTOs of this service's API

    public record AuditEventRequest(String workflowId, String agentName, @NotBlank String eventType,
                                    Map<String, Object> payload, String actor, String correlationId,
                                    String causationId) {
    }

    public record AuditEvent(long id, String workflowId, String agentName, String eventType, String payload,
                             String actor, String correlationId, String causationId, LocalDateTime createdAt) {
    }
}
