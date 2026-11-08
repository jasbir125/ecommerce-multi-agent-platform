package com.example.workflow;

import com.example.common.audit.AuditClient;
import com.example.common.events.EventPublisher;
import com.example.common.security.CallerHolder;
import com.example.common.util.Ids;
import com.example.common.web.ApiException;
import com.example.common.web.PlatformRestClient;
import com.example.workflow.WorkflowRecords.*;
import java.time.Instant;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;

@Service
public class WorkflowService {

    private static final Set<String> PRIVILEGED = Set.of("OPERATOR", "ADMIN", "AGENT");

    private final WorkflowRepository repo;
    private final InputGuardrails guardrails;
    private final EventPublisher events;
    private final AuditClient audit;
    private final TransactionTemplate tx;
    private final RestClient orders;
    private final RestClient auditApi;

    public WorkflowService(WorkflowRepository repo, InputGuardrails guardrails, EventPublisher events, AuditClient audit,
                           TransactionTemplate tx, PlatformRestClient clients,
                           @Value("${services.order-url}") String orderUrl, @Value("${services.audit-url}") String auditUrl) {
        this.repo = repo;
        this.guardrails = guardrails;
        this.events = events;
        this.audit = audit;
        this.tx = tx;
        this.orders = clients.forBaseUrl(orderUrl);
        this.auditApi = clients.forBaseUrl(auditUrl);
    }

    /** Creates a workflow once per requestId (retries return the same workflow). */
    public Workflow create(CreateRequest req) {
        String headerCustomer = CallerHolder.current().customerId();
        String customerId = headerCustomer != null && !headerCustomer.isBlank() ? headerCustomer : req.customerId();
        if (customerId == null || customerId.isBlank()) {
            throw new ApiException(HttpStatus.UNAUTHORIZED, "CUSTOMER_REQUIRED", "customer identity is required");
        }
        if (req.customerId() != null && !req.customerId().equals(customerId)) {
            audit.record(null, "workflow-service", "SECURITY_CUSTOMER_MISMATCH", customerId,
                    Map.of("claimedCustomer", req.customerId(), "orderId", req.orderId()));
            throw new ApiException(HttpStatus.FORBIDDEN, "CUSTOMER_MISMATCH", "customerId does not match the authenticated customer");
        }
        String requestId = req.requestId() != null && !req.requestId().isBlank() ? req.requestId() : UUID.randomUUID().toString();
        Optional<Workflow> already = repo.findByRequestId(requestId);
        if (already.isPresent()) {
            return already.get();
        }
        try {
            guardrails.check(req.message());
        } catch (ApiException e) {
            audit.record(null, "workflow-service", "GUARDRAIL_INPUT_REJECTED", customerId,
                    Map.of("code", e.code(), "orderId", req.orderId()));
            throw e;
        }
        // Ownership check: order-service answers 403/404 for orders the customer does not own.
        orders.get().uri("/api/orders/{id}/owner", req.orderId()).retrieve().toBodilessEntity();

        String workflowId = Ids.next("WF");
        Map<String, Object> state = new LinkedHashMap<>();
        state.put("customerMessage", req.message());
        state.put("intent", "DELAYED_ORDER_RECOVERY");
        state.put("errors", new ArrayList<String>());
        state.put("evidence", new ArrayList<String>());
        state.put("history", new ArrayList<Map<String, Object>>(List.of(entry(WorkflowStatus.STARTED, "created", null))));
        boolean inserted = Boolean.TRUE.equals(tx.execute(s -> {
            boolean ok = repo.insert(workflowId, requestId, customerId, req.orderId(), WorkflowStatus.STARTED, state);
            if (ok) {
                events.publish("workflow.started", workflowId, "WorkflowStarted", workflowId, null,
                        Map.of("workflowId", workflowId, "customerId", customerId, "orderId", req.orderId()));
            }
            return ok;
        }));
        if (!inserted) {
            return repo.findByRequestId(requestId).orElseThrow();
        }
        audit.record(workflowId, "workflow-service", "WORKFLOW_STARTED", customerId, Map.of("orderId", req.orderId()));
        return repo.findById(workflowId).orElseThrow();
    }

    public Workflow get(String workflowId) {
        Workflow w = repo.findById(workflowId)
                .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "WORKFLOW_NOT_FOUND", "Unknown workflow"));
        assertCanSee(w);
        return w;
    }

    public List<Workflow> list(String status) {
        CallerHolder.requireRole("OPERATOR", "ADMIN");
        return repo.list(status);
    }

    /**
     * Moves a workflow to a new state. Safe to repeat: a key already in the history
     * is a no-op. A stale read (another writer got there first) returns 409.
     */
    public Workflow resume(String workflowId, ResumeRequest req) {
        CallerHolder.requireRole("OPERATOR", "ADMIN", "AGENT");
        Workflow w = get(workflowId);
        return applyTransition(w, req.idempotencyKey(), req.targetStatus().toUpperCase(), req.statePatch(), req.reason());
    }

    /** Used by the Kafka listener (no HTTP caller). */
    public Workflow applyTransition(Workflow w, String key, String target, Map<String, Object> patch, String reason) {
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> history = (List<Map<String, Object>>) w.state().getOrDefault("history", new ArrayList<>());
        if (history.stream().anyMatch(h -> key.equals(h.get("key")))) {
            return w; // already applied
        }
        if (!WorkflowStatus.isKnown(target) || !WorkflowStatus.canMove(w.status(), target)) {
            throw new ApiException(HttpStatus.CONFLICT, "INVALID_TRANSITION", "Cannot move from " + w.status() + " to " + target);
        }
        Map<String, Object> state = new LinkedHashMap<>(w.state());
        if (patch != null) {
            state.putAll(patch);
        }
        List<Map<String, Object>> newHistory = new ArrayList<>(history);
        newHistory.add(entry(target, reason, key));
        state.put("history", newHistory);

        tx.executeWithoutResult(s -> {
            if (!repo.update(w.workflowId(), w.version(), target, state)) {
                throw new ApiException(HttpStatus.CONFLICT, "CONCURRENT_UPDATE", "Workflow changed concurrently; reload and retry");
            }
            publishTransition(w, target, reason);
        });
        audit.record(w.workflowId(), "workflow-service", "WORKFLOW_" + target, CallerHolder.current().customerId(),
                Map.of("from", w.status(), "reason", String.valueOf(reason)));
        return repo.findById(w.workflowId()).orElseThrow();
    }

    private void publishTransition(Workflow w, String target, String reason) {
        Map<String, Object> payload = Map.of("workflowId", w.workflowId(), "orderId", w.orderId(),
                "status", target, "reason", String.valueOf(reason));
        switch (target) {
            case WorkflowStatus.COMPLETED -> events.publish("order.recovery.completed", w.workflowId(), "OrderRecoveryCompleted", w.workflowId(), null, payload);
            case WorkflowStatus.FAILED -> events.publish("workflow.failed", w.workflowId(), "WorkflowFailed", w.workflowId(), null, payload);
            case WorkflowStatus.COMPENSATING -> events.publish("workflow.compensation.requested", w.workflowId(), "CompensationRequested", w.workflowId(), null, payload);
            default -> events.publish("workflow.agent.completed", w.workflowId(), "WorkflowStateChanged", w.workflowId(), null, payload);
        }
    }

    /** Workflow, state history, audit trail and the Kafka events it produced. */
    @SuppressWarnings("unchecked")
    public Map<String, Object> trace(String workflowId) {
        Workflow w = get(workflowId);
        List<Map<String, Object>> auditEvents;
        try {
            auditEvents = auditApi.get().uri("/api/audit/workflows/{id}", workflowId).retrieve().body(List.class);
        } catch (RestClientResponseException | org.springframework.web.client.ResourceAccessException e) {
            auditEvents = List.of(Map.of("eventType", "AUDIT_UNAVAILABLE"));
        }
        return Map.of("workflow", w, "history", w.state().getOrDefault("history", List.of()),
                "auditEvents", auditEvents, "kafkaEvents", repo.eventsFor(workflowId));
    }

    private void assertCanSee(Workflow w) {
        var caller = CallerHolder.current();
        boolean privileged = caller.roles().stream().anyMatch(PRIVILEGED::contains);
        if (!privileged && !w.customerId().equals(caller.customerId())) {
            audit.record(w.workflowId(), "workflow-service", "SECURITY_UNAUTHORIZED_WORKFLOW_ACCESS", caller.customerId(), Map.of());
            throw new ApiException(HttpStatus.FORBIDDEN, "NOT_WORKFLOW_OWNER", "You do not have access to this workflow");
        }
    }

    private static Map<String, Object> entry(String status, String reason, String key) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("status", status);
        m.put("at", Instant.now().toString());
        m.put("reason", reason);
        m.put("key", key);
        return m;
    }
}
