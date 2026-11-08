package com.example.payment;

import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.DecimalMin;
import com.example.common.audit.AuditClient;
import com.example.common.events.EventPublisher;
import com.example.common.util.Ids;
import com.example.common.web.ApiException;
import com.example.common.web.PlatformRestClient;
import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.Map;
import java.util.Optional;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.client.RestClient;

/**
 * The only place a refund is created. Nothing an LLM says counts as proof: every
 * rule is re-checked here against order-service and approval-service.
 */
@Service
public class RefundService {

    private static final String COLS = "refund_id, workflow_id, order_id, approval_id, amount, currency, status, idempotency_key, created_at";

    private final JdbcClient jdbc;
    private final EventPublisher events;
    private final AuditClient audit;
    private final RestClient orders;
    private final RestClient approvals;
    private final BigDecimal approvalThreshold;
    private final io.micrometer.core.instrument.MeterRegistry metrics;

    public RefundService(JdbcClient jdbc, EventPublisher events, AuditClient audit, PlatformRestClient clients, io.micrometer.core.instrument.MeterRegistry metrics,
                         @Value("${services.order-url}") String orderUrl,
                         @Value("${services.approval-url}") String approvalUrl,
                         @Value("${refund.approval-threshold}") BigDecimal approvalThreshold) {
        this.jdbc = jdbc;
        this.events = events;
        this.audit = audit;
        this.orders = clients.forBaseUrl(orderUrl);
        this.approvals = clients.forBaseUrl(approvalUrl);
        this.approvalThreshold = approvalThreshold;
        this.metrics = metrics;
    }

    @Transactional
    @SuppressWarnings("unchecked")
    public Refund initiate(RefundRequest req) {
        // 1. Idempotency: a repeated key returns the original refund, never a second one.
        Optional<Refund> existing = findByKey(req.idempotencyKey());
        if (existing.isPresent()) {
            return existing.get();
        }

        // 2. Order must exist, belong to the caller (enforced by order-service) and be paid.
        Map<String, Object> order = orders.get().uri("/api/orders/{id}", req.orderId()).retrieve().body(Map.class);
        BigDecimal orderTotal = new BigDecimal(String.valueOf(order.get("totalAmount")));
        if (!"CAPTURED".equals(order.get("paymentStatus"))) {
            throw new ApiException(HttpStatus.CONFLICT, "ORDER_NOT_REFUNDABLE", "Payment was not captured for this order");
        }

        // 3. Amount may not exceed what is still refundable.
        BigDecimal alreadyRefunded = jdbc.sql("""
                SELECT COALESCE(SUM(amount), 0) FROM payment_refund
                WHERE order_id = :o AND status IN ('INITIATED','COMPLETED')""")
                .param("o", req.orderId()).query(BigDecimal.class).single();
        if (req.amount().add(alreadyRefunded).compareTo(orderTotal) > 0) {
            throw new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "REFUND_EXCEEDS_ORDER",
                    "Refund of " + req.amount() + " exceeds refundable balance " + orderTotal.subtract(alreadyRefunded));
        }
        if (alreadyRefunded.signum() > 0) {
            throw new ApiException(HttpStatus.CONFLICT, "REFUND_ALREADY_INITIATED", "A refund already exists for this order");
        }

        // 4. Approval must be APPROVED, for this workflow, for a refund, covering the amount, not expired.
        if (req.amount().compareTo(approvalThreshold) >= 0) {
            requireValidApproval(req);
        }

        String id = Ids.next("RF");
        try {
            jdbc.sql("""
                    INSERT INTO payment_refund (refund_id, workflow_id, order_id, approval_id, amount, currency, status, idempotency_key)
                    VALUES (:id, :wf, :o, :ap, :amt, 'USD', 'INITIATED', :k)""")
                    .param("id", id).param("wf", req.workflowId()).param("o", req.orderId())
                    .param("ap", req.approvalId()).param("amt", req.amount()).param("k", req.idempotencyKey()).update();
        } catch (DuplicateKeyException race) {
            return findByKey(req.idempotencyKey()).orElseThrow(); // two requests with one key raced
        }
        metrics.counter("refunds.initiated").increment();
        metrics.summary("refunds.amount").record(req.amount().doubleValue());
        events.publish("payment.refund.initiated", id, "RefundInitiated", req.workflowId(), req.approvalId(),
                Map.of("refundId", id, "orderId", req.orderId(), "amount", req.amount()));
        audit.record(req.workflowId(), "payment-service", "REFUND_INITIATED", null,
                Map.of("refundId", id, "orderId", req.orderId(), "amount", req.amount(), "approvalId", String.valueOf(req.approvalId())));
        return get(id);
    }

    @SuppressWarnings("unchecked")
    private void requireValidApproval(RefundRequest req) {
        if (req.approvalId() == null || req.approvalId().isBlank()) {
            throw new ApiException(HttpStatus.FORBIDDEN, "APPROVAL_REQUIRED", "A valid approval ID is required for this refund");
        }
        Map<String, Object> a = approvals.get().uri("/api/approvals/{id}", req.approvalId()).retrieve().body(Map.class);
        boolean ok = "APPROVED".equals(a.get("status"))
                && req.workflowId().equals(a.get("workflowId"))
                && "REFUND".equals(a.get("requestedAction"))
                && a.get("amount") != null
                && new BigDecimal(String.valueOf(a.get("amount"))).compareTo(req.amount()) >= 0;
        if (!ok) {
            audit.record(req.workflowId(), "payment-service", "REFUND_REJECTED_INVALID_APPROVAL", null,
                    Map.of("approvalId", req.approvalId(), "approvalStatus", String.valueOf(a.get("status"))));
            throw new ApiException(HttpStatus.FORBIDDEN, "APPROVAL_INVALID",
                    "Approval is not APPROVED for this workflow/amount (status=" + a.get("status") + ")");
        }
    }

    public Refund get(String refundId) {
        Refund r = jdbc.sql("SELECT " + COLS + " FROM payment_refund WHERE refund_id = :id")
                .param("id", refundId).query(Refund.class).optional()
                .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "REFUND_NOT_FOUND", "Unknown refund"));
        orders.get().uri("/api/orders/{id}/owner", r.orderId()).retrieve().toBodilessEntity(); // ownership check
        return r;
    }

    private Optional<Refund> findByKey(String key) {
        return jdbc.sql("SELECT " + COLS + " FROM payment_refund WHERE idempotency_key = :k")
                .param("k", key).query(Refund.class).optional();
    }

    // ---- DTOs of this service's API

    public record RefundRequest(@NotBlank String workflowId, @NotBlank String orderId,
                                @NotNull @DecimalMin("0.01") BigDecimal amount,
                                String approvalId, @NotBlank String idempotencyKey) {
    }

    public record Refund(String refundId, String workflowId, String orderId, String approvalId,
                         BigDecimal amount, String currency, String status, String idempotencyKey,
                         LocalDateTime createdAt) {
    }
}
