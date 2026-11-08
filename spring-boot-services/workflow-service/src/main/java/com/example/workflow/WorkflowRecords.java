package com.example.workflow;

import jakarta.validation.constraints.NotBlank;
import java.time.LocalDateTime;
import java.util.Map;

public final class WorkflowRecords {

    private WorkflowRecords() {
    }

    public record CreateRequest(String customerId, @NotBlank String orderId, @NotBlank String message, String requestId) {
    }

    public record ResumeRequest(@NotBlank String idempotencyKey, @NotBlank String targetStatus,
                                Map<String, Object> statePatch, String reason) {
    }

    public record Workflow(String workflowId, String requestId, String customerId, String orderId, String status,
                           Map<String, Object> state, long version, LocalDateTime createdAt, LocalDateTime updatedAt) {
    }
}
