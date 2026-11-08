package com.example.workflow.ai;

import java.math.BigDecimal;
import java.util.List;

/** Structured model output for a recovery proposal. The model only proposes; services execute. */
public record ResolutionDecision(String action, BigDecimal refundAmount, String replacementProductId,
                                 boolean approvalRequired, String riskLevel, String reason, List<String> evidence) {
}
