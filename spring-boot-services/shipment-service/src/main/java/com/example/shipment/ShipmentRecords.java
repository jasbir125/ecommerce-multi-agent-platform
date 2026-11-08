package com.example.shipment;

import java.time.LocalDate;

public final class ShipmentRecords {

    private ShipmentRecords() {
    }

    /** exceptionType is derived by us from carrier data: NONE, DELAYED or LOST. */
    public record Shipment(String orderId, String customerId, String carrier, String trackingNo, String status,
                           LocalDate expectedDelivery, int delayDays, String lastEvent, String exceptionType) {
    }

    public record CarrierCase(String caseId, String orderId, String workflowId, String reason, boolean created) {
    }

    public record CarrierCaseRequest(String workflowId, String reason) {
    }
}
