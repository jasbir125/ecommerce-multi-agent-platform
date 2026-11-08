package com.example.audit;

import jakarta.validation.constraints.NotBlank;
import java.time.LocalDateTime;
import java.util.Map;

public final class AuditRecords {

    private AuditRecords() {
    }

    public record AuditEventRequest(String workflowId, String agentName, @NotBlank String eventType,
                                    Map<String, Object> payload, String actor, String correlationId,
                                    String causationId) {
    }

    public record AuditEvent(long id, String workflowId, String agentName, String eventType, String payload,
                             String actor, String correlationId, String causationId, LocalDateTime createdAt) {
    }
}
