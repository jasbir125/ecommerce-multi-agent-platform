package com.example.order;

import java.math.BigDecimal;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;

/** DTOs for the order API. */
public final class OrderRecords {

    private OrderRecords() {
    }

    public record Order(String orderId, String customerId, String status, String paymentStatus,
                        BigDecimal totalAmount, String currency, String productId, String productName,
                        Integer quantity, String replacesOrder, long version) {
    }

    public record OwnerResponse(String orderId, String customerId, boolean ownedByCaller) {
    }

    public record CancelResponse(String orderId, String status, boolean cancelled) {
    }

    public record ReplacementRequest(@NotBlank String workflowId, @NotBlank String originalOrderId,
                                     @NotBlank String reservationId, @NotBlank String idempotencyKey) {
    }

    public record ReplacementResponse(String replacementOrderId, String originalOrderId, String productId,
                                      BigDecimal amount, boolean created) {
    }
}
