package com.example.policy;

import jakarta.validation.constraints.NotBlank;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

public final class PolicyRecords {

    private PolicyRecords() {
    }

    public record IngestRequest(@NotBlank String policyKey, @NotBlank String version, @NotBlank String title,
                                @NotBlank String content, String region, String policyType,
                                LocalDate effectiveFrom, LocalDate effectiveTo) {
    }

    public record IngestResult(long policyId, String policyKey, String version, int chunks, boolean created,
                               boolean injectionSuspected) {
    }

    public record PolicyDoc(long id, String policyKey, String version, String title, String content,
                            LocalDate effectiveFrom, LocalDate effectiveTo, String region) {
    }

    public record SearchRequest(@NotBlank String query, String region, String policyType, Integer topK,
                                LocalDate asOfDate) {
    }

    /** One citation. Retrieved snippet text is untrusted data, never instructions. */
    public record Source(String policyId, String policyKey, String version, String source, double score,
                         double rerankScore, String snippet, boolean injectionSuspected) {
    }

    public record RagResponse(String answer, double confidence, boolean grounded, String policyVersion,
                              List<Source> sources, boolean insufficientEvidence) {
    }

    public record ValidateRequest(@NotBlank String orderId, String orderStatus, BigDecimal orderAmount,
                                  BigDecimal refundAmount, Integer delayDays, String shipmentStatus,
                                  String region) {
    }

    public record ValidationResult(boolean refundEligible, boolean approvalRequired, List<String> reasons,
                                   String policyId, String policyKey, String policyVersion, String citation) {
    }
}
