package com.example.notification;

import com.example.common.audit.AuditClient;
import com.example.common.events.EventPublisher;
import com.example.common.web.ApiException;
import com.example.common.web.PlatformRestClient;
import com.example.notification.NotificationRecords.*;
import java.util.Map;
import java.util.Set;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.client.RestClient;

/**
 * Sends customer messages from fixed templates only, so free-form model text can
 * never state a business result. Templates that report a result (refund,
 * replacement) are sent only after the result is re-verified with the owning service.
 */
@Service
public class NotificationService {

    private static final Logger log = LoggerFactory.getLogger(NotificationService.class);

    private static final Map<String, String> TEMPLATES = Map.of(
            "REFUND_INITIATED", "Your refund {refundId} for order {orderId} has been initiated.",
            "REPLACEMENT_CREATED", "Your replacement order {replacementOrderId} has been created.",
            "MANUAL_REVIEW", "Your request for order {orderId} needs a manual review. Our support team will contact you.",
            "SHIPMENT_VERIFICATION_FAILED", "We could not verify the shipment status for order {orderId} right now. Our support team will follow up.",
            "REQUEST_RECEIVED", "We received your request for order {orderId} and are working on it.");
    private static final Set<String> VERIFIED_TEMPLATES = Set.of("REFUND_INITIATED", "REPLACEMENT_CREATED");

    private final JdbcClient jdbc;
    private final EventPublisher events;
    private final AuditClient audit;
    private final RestClient payments;
    private final RestClient orders;
    private final io.micrometer.core.instrument.MeterRegistry metrics;

    public NotificationService(JdbcClient jdbc, EventPublisher events, AuditClient audit, PlatformRestClient clients, io.micrometer.core.instrument.MeterRegistry metrics,
                               @Value("${services.payment-url}") String paymentUrl,
                               @Value("${services.order-url}") String orderUrl) {
        this.jdbc = jdbc;
        this.events = events;
        this.audit = audit;
        this.payments = clients.forBaseUrl(paymentUrl);
        this.orders = clients.forBaseUrl(orderUrl);
        this.metrics = metrics;
    }

    @Transactional
    public Result sendToCustomer(CustomerNotification n) {
        String template = TEMPLATES.get(n.template());
        if (template == null) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "UNKNOWN_TEMPLATE", "Unknown template " + n.template());
        }
        Map<String, String> params = n.params() == null ? Map.of() : n.params();
        verifyFacts(n.template(), params);
        String text = template;
        for (var e : params.entrySet()) {
            text = text.replace("{" + e.getKey() + "}", e.getValue());
        }
        if (text.contains("{")) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "MISSING_PARAMS", "Template parameters are missing");
        }
        String channel = n.channel() == null ? "EMAIL" : n.channel().toUpperCase();
        long id = jdbc.sql("""
                INSERT INTO notification_record (workflow_id, channel, recipient, template, status, sent_at)
                VALUES (:wf, :ch, :rcpt, :tpl, 'SENT', now()) RETURNING id""")
                .param("wf", n.workflowId()).param("ch", channel).param("rcpt", n.customerId())
                .param("tpl", n.template()).query(Long.class).single();
        // Delivery is simulated; the recipient is masked in logs.
        log.info("NOTIFY channel={} recipient={} template={}", channel, mask(n.customerId()), n.template());
        metrics.counter("notifications.sent", "template", n.template()).increment();
        events.publish("customer.notification.sent", String.valueOf(id), "CustomerNotified", n.workflowId(), null,
                Map.of("notificationId", id, "template", n.template(), "channel", channel));
        audit.record(n.workflowId(), "notification-service", "CUSTOMER_NOTIFIED", null,
                Map.of("notificationId", id, "template", n.template()));
        return new Result(id, "SENT", text);
    }

    @Transactional
    public Result sendInternal(InternalNotification n) {
        String channel = n.channel() == null ? "SLACK" : n.channel().toUpperCase();
        long id = jdbc.sql("""
                INSERT INTO notification_record (workflow_id, channel, recipient, template, status, sent_at)
                VALUES (:wf, :ch, 'internal', 'INTERNAL', 'SENT', now()) RETURNING id""")
                .param("wf", n.workflowId() == null ? "N/A" : n.workflowId()).param("ch", channel)
                .query(Long.class).single();
        log.info("NOTIFY-INTERNAL channel={} id={}", channel, id);
        return new Result(id, "SENT", n.message());
    }

    /** Result-style templates must match what the owning service reports. */
    @SuppressWarnings("unchecked")
    private void verifyFacts(String template, Map<String, String> params) {
        if (!VERIFIED_TEMPLATES.contains(template)) {
            return;
        }
        if (template.equals("REFUND_INITIATED")) {
            Map<String, Object> r = payments.get().uri("/api/payments/refunds/{id}", params.get("refundId")).retrieve().body(Map.class);
            if (r == null || !Set.of("INITIATED", "COMPLETED").contains(String.valueOf(r.get("status")))) {
                throw new ApiException(HttpStatus.CONFLICT, "UNVERIFIED_RESULT", "Refund is not verified as initiated");
            }
        } else {
            Map<String, Object> o = orders.get().uri("/api/orders/{id}", params.get("replacementOrderId")).retrieve().body(Map.class);
            if (o == null || o.get("replacesOrder") == null) {
                throw new ApiException(HttpStatus.CONFLICT, "UNVERIFIED_RESULT", "Replacement order is not verified");
            }
        }
    }

    private static String mask(String s) {
        return s == null || s.length() < 4 ? "***" : s.substring(0, 2) + "***" + s.substring(s.length() - 2);
    }
}
