package com.example.policy;

import com.example.common.web.ApiException;
import com.example.policy.PolicyRecords.*;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;

/**
 * Final, deterministic refund-eligibility check. RAG explains the policy; this
 * class decides. No model is involved, so the result is repeatable and auditable.
 */
@Service
public class ValidationService {

    private final JdbcClient jdbc;
    private final int minDelayDays;
    private final BigDecimal approvalFrom;

    public ValidationService(JdbcClient jdbc, @Value("${policy.refund.min-delay-days}") int minDelayDays,
                             @Value("${policy.refund.approval-required-from}") BigDecimal approvalFrom) {
        this.jdbc = jdbc;
        this.minDelayDays = minDelayDays;
        this.approvalFrom = approvalFrom;
    }

    public ValidationResult validate(ValidateRequest req) {
        String region = req.region() == null || req.region().isBlank() ? "ALL" : req.region().toUpperCase();
        var doc = jdbc.sql("""
                SELECT id, policy_key, version, title FROM policy_document
                WHERE policy_key = 'refund-policy' AND status = 'ACTIVE'
                  AND (effective_from IS NULL OR effective_from <= :d) AND (effective_to IS NULL OR effective_to >= :d)
                  AND (region = 'ALL' OR region = :r)
                ORDER BY effective_from DESC NULLS LAST, id DESC LIMIT 1""")
                .param("d", LocalDate.now()).param("r", region).query().listOfRows().stream().findFirst();
        if (doc.isEmpty()) {
            // No authoritative policy => never guess eligibility.
            return new ValidationResult(false, true, List.of("INSUFFICIENT_POLICY_EVIDENCE"), null, null, null, null);
        }
        List<String> reasons = new ArrayList<>();
        boolean eligible = true;

        if ("CANCELLED".equalsIgnoreCase(req.orderStatus())) {
            eligible = false;
            reasons.add("ORDER_ALREADY_CANCELLED");
        }
        boolean lost = "LOST".equalsIgnoreCase(req.shipmentStatus());
        int delay = req.delayDays() == null ? 0 : req.delayDays();
        if (lost) {
            reasons.add("SHIPMENT_LOST");
        } else if (delay >= minDelayDays) {
            reasons.add("DELAY_" + delay + "_DAYS_MEETS_THRESHOLD_" + minDelayDays);
        } else {
            eligible = false;
            reasons.add("DELAY_BELOW_THRESHOLD_" + minDelayDays);
        }
        if (req.refundAmount() != null && req.orderAmount() != null && req.refundAmount().compareTo(req.orderAmount()) > 0) {
            eligible = false;
            reasons.add("REFUND_EXCEEDS_ORDER_AMOUNT");
        }
        BigDecimal amount = req.refundAmount() != null ? req.refundAmount() : req.orderAmount();
        boolean approval = amount == null || amount.compareTo(approvalFrom) >= 0;

        String key = (String) doc.get().get("policy_key");
        String version = (String) doc.get().get("version");
        String id = String.valueOf(doc.get().get("id"));
        return new ValidationResult(eligible, approval, reasons, id, key, version, key + " v" + version);
    }

    public PolicyDoc byId(long id) {
        return jdbc.sql("SELECT id, policy_key, version, title, content, policy_type, status, effective_from, effective_to, region FROM policy_document WHERE id = :id")
                .param("id", id).query(PolicyDoc.class).optional()
                .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "POLICY_NOT_FOUND", "Unknown policy"));
    }

    public PolicyDoc byKeyVersion(String key, String version) {
        return jdbc.sql("SELECT id, policy_key, version, title, content, policy_type, status, effective_from, effective_to, region FROM policy_document WHERE policy_key = :k AND version = :v")
                .param("k", key).param("v", version).query(PolicyDoc.class).optional()
                .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "POLICY_NOT_FOUND", "Unknown policy version"));
    }
}
