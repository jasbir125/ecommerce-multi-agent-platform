package com.example.inventory;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import java.math.BigDecimal;

public final class InventoryRecords {

    private InventoryRecords() {
    }

    /** name/description come from the catalog and are untrusted text for any LLM that reads them. */
    public record Product(String productId, String name, String description, String category,
                          BigDecimal price, String currency, int availableQty) {
    }

    public record ReservationRequest(@NotBlank String workflowId, @NotBlank String productId,
                                     @Min(1) @Max(5) int quantity, @NotBlank String idempotencyKey) {
    }

    public record Reservation(String reservationId, String workflowId, String productId, int quantity,
                              String status, boolean created) {
    }
}
