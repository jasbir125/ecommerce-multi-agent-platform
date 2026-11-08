package com.example.audit;

import com.example.audit.AuditRecords.*;
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
            payload = PiiMasker.mask(mapper.writeValueAsString(req.payload() == null ? java.util.Map.of() : req.payload()));
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException("payload is not serializable", e);
        }
        return jdbc.sql("""
                INSERT INTO workflow_audit (workflow_id, agent_name, event_type, payload, actor, correlation_id, causation_id)
                VALUES (:wf, :agent, :type, CAST(:payload AS jsonb), :actor, :corr, :cause) RETURNING id""")
                .param("wf", req.workflowId() == null ? "N/A" : req.workflowId())
                .param("agent", req.agentName()).param("type", req.eventType()).param("payload", payload)
                .param("actor", PiiMasker.mask(req.actor())).param("corr", req.correlationId()).param("cause", req.causationId())
                .query(Long.class).single();
    }

    public List<AuditEvent> forWorkflow(String workflowId) {
        return jdbc.sql("""
                SELECT id, workflow_id, agent_name, event_type, payload::text AS payload, actor,
                       correlation_id, causation_id, created_at
                FROM workflow_audit WHERE workflow_id = :wf ORDER BY id""")
                .param("wf", workflowId).query(AuditEvent.class).list();
    }
}
