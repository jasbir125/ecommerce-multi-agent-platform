package com.example.shipment;

import com.example.common.audit.AuditClient;
import com.example.common.events.EventPublisher;
import com.example.common.security.CallerHolder;
import com.example.common.util.Ids;
import com.example.common.web.ApiException;
import com.example.shipment.CarrierGateway.Row;
import com.example.shipment.ShipmentRecords.*;
import java.util.List;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ShipmentService {

    private static final int DELAY_THRESHOLD_DAYS = 3;

    private final CarrierGateway carrier;
    private final JdbcClient jdbc;
    private final EventPublisher events;
    private final AuditClient audit;

    public ShipmentService(CarrierGateway carrier, JdbcClient jdbc, EventPublisher events, AuditClient audit) {
        this.carrier = carrier;
        this.jdbc = jdbc;
        this.events = events;
        this.audit = audit;
    }

    public Shipment get(String orderId) {
        Row r = loadOwned(orderId);
        String type = "LOST".equals(r.status()) ? "LOST" : r.delayDays() >= DELAY_THRESHOLD_DAYS ? "DELAYED" : "NONE";
        return new Shipment(r.orderId(), r.customerId(), r.carrier(), r.trackingNo(), r.status(),
                r.expectedDelivery(), r.delayDays(), r.lastEvent(), type);
    }

    /** One carrier case per shipment exception; repeats return the existing case. */
    @Transactional
    public CarrierCase createCase(String orderId, CarrierCaseRequest req) {
        Shipment s = get(orderId);
        if ("NONE".equals(s.exceptionType())) {
            throw new ApiException(HttpStatus.CONFLICT, "NO_SHIPMENT_EXCEPTION", "Shipment has no delay or loss to report");
        }
        List<Map<String, Object>> existing = jdbc.sql("SELECT case_id, workflow_id, reason FROM carrier_case WHERE order_id = :o")
                .param("o", orderId).query().listOfRows();
        if (!existing.isEmpty()) {
            var row = existing.get(0);
            return new CarrierCase((String) row.get("case_id"), orderId, (String) row.get("workflow_id"),
                    (String) row.get("reason"), false);
        }
        String caseId = Ids.next("CC");
        String reason = req != null && req.reason() != null ? req.reason() : s.exceptionType();
        String wf = req != null ? req.workflowId() : null;
        // ON CONFLICT covers two concurrent requests racing past the check above.
        int inserted = jdbc.sql("""
                INSERT INTO carrier_case (case_id, order_id, workflow_id, reason) VALUES (:c, :o, :w, :r)
                ON CONFLICT (order_id) DO NOTHING""")
                .param("c", caseId).param("o", orderId).param("w", wf).param("r", reason).update();
        if (inserted == 0) {
            return createCase(orderId, req);
        }
        events.publish("shipment.exceptions", orderId, "ShipmentExceptionRaised", wf, null,
                Map.of("orderId", orderId, "caseId", caseId, "exceptionType", s.exceptionType(), "delayDays", s.delayDays()));
        audit.record(wf, "shipment-service", "CARRIER_CASE_CREATED", CallerHolder.current().customerId(),
                Map.of("orderId", orderId, "caseId", caseId));
        return new CarrierCase(caseId, orderId, wf, reason, true);
    }

    private Row loadOwned(String orderId) {
        String caller = CallerHolder.requireCustomerId();
        Row r = carrier.track(orderId)
                .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "SHIPMENT_NOT_FOUND", "No shipment for this order"));
        if (!r.customerId().equals(caller)) {
            audit.record(null, "shipment-service", "SECURITY_UNAUTHORIZED_SHIPMENT_ACCESS", caller, Map.of("orderId", orderId));
            throw new ApiException(HttpStatus.FORBIDDEN, "NOT_ORDER_OWNER", "You do not have access to this shipment");
        }
        return r;
    }
}
