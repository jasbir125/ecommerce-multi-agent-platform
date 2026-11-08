package com.example.order;

import com.example.common.audit.AuditClient;
import com.example.common.events.EventPublisher;
import com.example.common.security.CallerHolder;
import com.example.common.util.Ids;
import com.example.common.web.ApiException;
import com.example.common.web.PlatformRestClient;
import com.example.order.OrderRecords.*;
import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.client.RestClient;

/** Order rules: ownership, cancellation eligibility and idempotent replacement creation. */
@Service
public class OrderService {

    private final OrderRepository orders;
    private final JdbcClient jdbc;
    private final EventPublisher events;
    private final AuditClient audit;
    private final RestClient inventory;

    public OrderService(OrderRepository orders, JdbcClient jdbc, EventPublisher events, AuditClient audit,
                        PlatformRestClient clients, @Value("${services.inventory-url}") String inventoryUrl) {
        this.orders = orders;
        this.jdbc = jdbc;
        this.events = events;
        this.audit = audit;
        this.inventory = clients.forBaseUrl(inventoryUrl);
    }

    /**
     * Loads an order the caller owns. A missing order and someone else's order
     * produce different codes internally but the audit trail records the attempt.
     */
    public Order getOwned(String orderId) {
        String caller = CallerHolder.requireCustomerId();
        Order order = orders.find(orderId)
                .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "ORDER_NOT_FOUND", "Order not found"));
        if (!order.customerId().equals(caller)) {
            audit.record(null, "order-service", "SECURITY_UNAUTHORIZED_ORDER_ACCESS", caller,
                    Map.of("orderId", orderId, "attemptedBy", caller));
            throw new ApiException(HttpStatus.FORBIDDEN, "NOT_ORDER_OWNER", "You do not have access to this order");
        }
        return order;
    }

    public List<Order> listForCustomer(String customerId) {
        if (!customerId.equals(CallerHolder.requireCustomerId())) {
            throw new ApiException(HttpStatus.FORBIDDEN, "NOT_CUSTOMER", "Cannot list another customer's orders");
        }
        return orders.findByCustomer(customerId);
    }

    public OwnerResponse owner(String orderId) {
        Order o = getOwned(orderId); // throws 403/404 if not the owner
        return new OwnerResponse(o.orderId(), o.customerId(), true);
    }

    @Transactional
    public CancelResponse cancel(String orderId) {
        Order o = getOwned(orderId);
        if ("CANCELLED".equals(o.status())) {
            return new CancelResponse(orderId, "CANCELLED", false); // idempotent: already cancelled
        }
        if (!List.of("CONFIRMED", "PROCESSING").contains(o.status())) {
            throw new ApiException(HttpStatus.CONFLICT, "ORDER_NOT_CANCELLABLE",
                    "Order is " + o.status() + " and cannot be cancelled directly; use a carrier case/refund flow");
        }
        if (!orders.cancel(orderId, o.version())) {
            throw new ApiException(HttpStatus.CONFLICT, "CONCURRENT_UPDATE", "Order changed concurrently; retry");
        }
        events.publish("order.cancelled", orderId, "OrderCancelled", null, null,
                Map.of("orderId", orderId, "customerId", o.customerId()));
        audit.record(null, "order-service", "ORDER_CANCELLED", o.customerId(), Map.of("orderId", orderId));
        return new CancelResponse(orderId, "CANCELLED", true);
    }

    /**
     * Creates the replacement order. Guardrails: the caller must own the original
     * order, an inventory reservation for this workflow must already be RESERVED,
     * and repeating the same idempotency key returns the first result.
     */
    @Transactional
    @SuppressWarnings("unchecked")
    public ReplacementResponse createReplacement(ReplacementRequest req) {
        var existing = jdbc.sql("SELECT replacement_order_id, original_order_id FROM order_replacement WHERE idempotency_key = :k")
                .param("k", req.idempotencyKey()).query().listOfRows();
        if (!existing.isEmpty()) {
            String rid = (String) existing.get(0).get("replacement_order_id");
            Order r = orders.find(rid).orElseThrow();
            return new ReplacementResponse(rid, req.originalOrderId(), r.productId(), r.totalAmount(), false);
        }
        Order original = getOwned(req.originalOrderId());

        Map<String, Object> reservation = inventory.get()
                .uri("/api/inventory/reservations/{id}", req.reservationId()).retrieve().body(Map.class);
        if (reservation == null || !"RESERVED".equals(reservation.get("status"))
                || !req.workflowId().equals(reservation.get("workflowId"))) {
            throw new ApiException(HttpStatus.CONFLICT, "RESERVATION_REQUIRED",
                    "A RESERVED inventory reservation for this workflow is required before creating a replacement");
        }
        String productId = (String) reservation.get("productId");
        int qty = ((Number) reservation.get("quantity")).intValue();
        Map<String, Object> product = inventory.get().uri("/api/inventory/{id}", productId).retrieve().body(Map.class);
        BigDecimal amount = new BigDecimal(String.valueOf(product.get("price"))).multiply(BigDecimal.valueOf(qty));

        String newId = Ids.next("ORD");
        jdbc.sql("""
                INSERT INTO customer_order (order_id, customer_id, status, payment_status, total_amount,
                                            product_id, product_name, quantity, replaces_order)
                VALUES (:id, :c, 'CONFIRMED', 'PENDING', :amt, :p, :pn, :q, :orig)""")
                .param("id", newId).param("c", original.customerId()).param("amt", amount)
                .param("p", productId).param("pn", product.get("name")).param("q", qty)
                .param("orig", original.orderId()).update();
        jdbc.sql("""
                INSERT INTO order_replacement (replacement_order_id, original_order_id, workflow_id, reservation_id, idempotency_key)
                VALUES (:id, :orig, :wf, :res, :key)""")
                .param("id", newId).param("orig", original.orderId()).param("wf", req.workflowId())
                .param("res", req.reservationId()).param("key", req.idempotencyKey()).update();
        events.publish("order.replacement.created", newId, "ReplacementOrderCreated", req.workflowId(), null,
                Map.of("replacementOrderId", newId, "originalOrderId", original.orderId(),
                        "productId", productId, "amount", amount));
        audit.record(req.workflowId(), "order-service", "REPLACEMENT_ORDER_CREATED", original.customerId(),
                Map.of("replacementOrderId", newId, "reservationId", req.reservationId()));
        return new ReplacementResponse(newId, original.orderId(), productId, amount, true);
    }
}
