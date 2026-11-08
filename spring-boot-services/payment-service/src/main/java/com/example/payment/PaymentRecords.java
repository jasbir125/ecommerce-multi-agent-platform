package com.example.payment;

import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import java.math.BigDecimal;
import java.time.LocalDateTime;

public final class PaymentRecords {

    private PaymentRecords() {
    }

    public record RefundRequest(@NotBlank String workflowId, @NotBlank String orderId,
                                @NotNull @DecimalMin("0.01") BigDecimal amount,
                                String approvalId, @NotBlank String idempotencyKey) {
    }

    public record Refund(String refundId, String workflowId, String orderId, String approvalId,
                         BigDecimal amount, String currency, String status, String idempotencyKey,
                         LocalDateTime createdAt) {
    }
}
