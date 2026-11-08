package com.example.workflow.ai;

import com.example.common.web.ApiException;
import java.math.BigDecimal;
import java.util.Set;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

/** Second guardrail layer: deterministic checks on whatever the model proposed. */
@Component
public class OutputGuardrails {

    static final Set<String> ACTIONS = Set.of("REFUND_AND_REPLACE", "REFUND_ONLY", "REPLACE_ONLY", "ESCALATE", "NO_ACTION");
    static final Set<String> RISK = Set.of("LOW", "MEDIUM", "HIGH");

    public void validate(ResolutionDecision d, BigDecimal orderAmount, boolean refundEligible) {
        if (d == null || d.action() == null || !ACTIONS.contains(d.action())) {
            throw reject("INVALID_ACTION", "Action is not in the allowed set");
        }
        if (d.riskLevel() == null || !RISK.contains(d.riskLevel())) {
            throw reject("INVALID_RISK_LEVEL", "Risk level must be LOW, MEDIUM or HIGH");
        }
        boolean refunds = d.action().startsWith("REFUND");
        if (refunds && !refundEligible) {
            throw reject("POLICY_FORBIDS_REFUND", "Policy says the refund is not eligible");
        }
        if (refunds && (d.refundAmount() == null || d.refundAmount().signum() <= 0
                || (orderAmount != null && d.refundAmount().compareTo(orderAmount) > 0))) {
            throw reject("INVALID_REFUND_AMOUNT", "Refund amount is missing or exceeds the order amount");
        }
        if (refunds && !d.approvalRequired()) {
            throw reject("APPROVAL_REQUIRED", "Refunds always require approval");
        }
        if (d.evidence() == null || d.evidence().isEmpty()) {
            throw reject("EVIDENCE_REQUIRED", "A decision must cite evidence");
        }
    }

    private ApiException reject(String code, String msg) {
        return new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "OUTPUT_REJECTED_" + code, msg);
    }
}
