package com.example.approval;

import java.math.BigDecimal;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.NotBlank;
import com.example.common.audit.AuditClient;
import com.example.common.events.EventPublisher;
import com.example.common.security.CallerHolder;
import com.example.common.util.Ids;
import com.example.common.web.ApiException;
import java.time.LocalDateTime;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ApprovalService {

    private static final String COLS = """
            approval_id, workflow_id, status, requested_action, amount, reason, risk_level,
            decided_by, decided_at, expires_at, created_at""";

    private final JdbcClient jdbc;
    private final EventPublisher events;
    private final AuditClient audit;
    private final long ttlMinutes;
    private final io.micrometer.core.instrument.MeterRegistry metrics;

    public ApprovalService(JdbcClient jdbc, EventPublisher events, AuditClient audit, io.micrometer.core.instrument.MeterRegistry metrics,
                           @Value("${approval.ttl-minutes}") long ttlMinutes) {
        this.metrics = metrics;
        this.jdbc = jdbc;
        this.events = events;
        this.audit = audit;
        this.ttlMinutes = ttlMinutes;
    }

    @Transactional
    public Approval create(CreateRequest req) {
        String id = Ids.next("APR");
        jdbc.sql("""
                INSERT INTO approval_request (approval_id, workflow_id, status, requested_action, amount, reason, risk_level, expires_at)
                VALUES (:id, :wf, 'PENDING', :act, :amt, :reason, :risk, :exp)""")
                .param("id", id).param("wf", req.workflowId()).param("act", req.requestedAction())
                .param("amt", req.amount()).param("reason", req.reason()).param("risk", req.riskLevel())
                .param("exp", LocalDateTime.now().plusMinutes(ttlMinutes)).update();
        Map<String, Object> payload = new HashMap<>();
        payload.put("approvalId", id);
        payload.put("action", req.requestedAction());
        payload.put("amount", req.amount());
        payload.put("riskLevel", req.riskLevel());
        metrics.counter("approval.requests").increment();
        events.publish("workflow.approval.requested", id, "ApprovalRequested", req.workflowId(), null, payload);
        audit.record(req.workflowId(), "approval-service", "APPROVAL_REQUESTED", CallerHolder.current().customerId(), payload);
        return get(id);
    }

    /** Reads an approval and lazily marks it EXPIRED once its deadline has passed. */
    @Transactional
    public Approval get(String id) {
        Approval a = jdbc.sql("SELECT " + COLS + " FROM approval_request WHERE approval_id = :id")
                .param("id", id).query(Approval.class).optional()
                .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "APPROVAL_NOT_FOUND", "Unknown approval"));
        if ("PENDING".equals(a.status()) && a.expiresAt() != null && a.expiresAt().isBefore(LocalDateTime.now())) {
            jdbc.sql("UPDATE approval_request SET status = 'EXPIRED' WHERE approval_id = :id AND status = 'PENDING'")
                    .param("id", id).update();
            metrics.counter("approval.decisions", "decision", "EXPIRED").increment();
            audit.record(a.workflowId(), "approval-service", "APPROVAL_EXPIRED", null, Map.of("approvalId", id));
            return jdbc.sql("SELECT " + COLS + " FROM approval_request WHERE approval_id = :id")
                    .param("id", id).query(Approval.class).single();
        }
        return a;
    }

    public List<Approval> list(String status) {
        return jdbc.sql("SELECT " + COLS + " FROM approval_request WHERE (CAST(:s AS text) IS NULL OR status = :s) ORDER BY created_at DESC LIMIT 100")
                .param("s", status == null || status.isBlank() ? null : status.toUpperCase())
                .query(Approval.class).list();
    }

    /** Only APPROVER/MANAGER roles may decide, and only while the request is still PENDING. */
    @Transactional
    public Approval decide(String id, DecisionRequest req) {
        CallerHolder.requireRole("APPROVER", "MANAGER");
        String decision = req.decision().toUpperCase();
        if (!decision.equals("APPROVED") && !decision.equals("REJECTED")) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "INVALID_DECISION", "decision must be APPROVED or REJECTED");
        }
        Approval current = get(id); // also applies expiry
        if (!"PENDING".equals(current.status())) {
            throw new ApiException(HttpStatus.CONFLICT, "APPROVAL_NOT_PENDING", "Approval is already " + current.status());
        }
        int updated = jdbc.sql("""
                UPDATE approval_request SET status = :s, decided_by = :by, decided_at = now()
                WHERE approval_id = :id AND status = 'PENDING'""")
                .param("s", decision).param("by", req.decidedBy()).param("id", id).update();
        if (updated == 0) {
            throw new ApiException(HttpStatus.CONFLICT, "APPROVAL_NOT_PENDING", "Approval was decided concurrently");
        }
        Map<String, Object> payload = new HashMap<>();
        payload.put("approvalId", id);
        payload.put("decision", decision);
        payload.put("decidedBy", req.decidedBy());
        payload.put("comment", req.comment());
        metrics.counter("approval.decisions", "decision", decision).increment();
        metrics.timer("approval.decision.time", "decision", decision).record(java.time.Duration.between(current.createdAt(), java.time.LocalDateTime.now()));
        events.publish("workflow.approval.decided", id, "ApprovalDecided", current.workflowId(), null, payload);
        audit.record(current.workflowId(), "approval-service", "APPROVAL_" + decision, req.decidedBy(), payload);
        return get(id);
    }

    // ---- DTOs of this service's API

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
