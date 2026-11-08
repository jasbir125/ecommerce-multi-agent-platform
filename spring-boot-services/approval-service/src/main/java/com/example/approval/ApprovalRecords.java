package com.example.approval;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import java.math.BigDecimal;
import java.time.LocalDateTime;

public final class ApprovalRecords {

    private ApprovalRecords() {
    }

    public record Approval(String approvalId, String workflowId, String status, String requestedAction,
                           BigDecimal amount, String reason, String riskLevel, String decidedBy,
                           LocalDateTime decidedAt, LocalDateTime expiresAt, LocalDateTime createdAt) {
    }

    public record CreateRequest(@NotBlank String workflowId, @NotBlank String requestedAction,
                                BigDecimal amount, String reason, String riskLevel) {
    }

    /** decision must be APPROVED or REJECTED. */
    public record DecisionRequest(@NotNull String decision, @NotBlank String decidedBy, String comment) {
    }
}
