package com.example.policy;

import jakarta.validation.constraints.NotBlank;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;

public final class PolicyRecords {

    private PolicyRecords() {
    }

    /**
     * A document to ingest. contentType is text/plain (default), text/markdown or text/html.
     * Filter fields (region, tenant, segment, category) decide who can retrieve the policy.
     */
    public record IngestRequest(@NotBlank String policyKey, @NotBlank String version, @NotBlank String title,
                                @NotBlank String content, String contentType, String region, String policyType,
                                String tenantId, String customerSegment, String productCategory, String sourceUri,
                                LocalDate effectiveFrom, LocalDate effectiveTo) {
    }

    public record IngestJob(String jobId, String status, String policyKey, String version,
                            Map<String, Object> stats, String error, Long documentId) {
    }

    public record PolicyDoc(long id, String policyKey, String version, String title, String content, String policyType,
                            String status, LocalDate effectiveFrom, LocalDate effectiveTo, String region) {
    }

    public record PolicySummary(long id, String policyKey, String version, String title, String policyType, String status,
                                String region, LocalDate effectiveFrom, LocalDate effectiveTo) {
    }

    /**
     * mode: hybrid (default), vector or keyword. version pins an exact policy version.
     * Null filters mean: region -> global documents only, tenant -> "default", segment/category -> no filter.
     */
    public record SearchRequest(@NotBlank String query, String region, String policyType, String tenantId,
                                String customerSegment, String productCategory, String version, Integer topK,
                                LocalDate asOfDate, String mode, Boolean debug) {
    }

    /** One citation. text is the compressed evidence; it is untrusted data, never instructions. */
    public record Source(String policyId, String chunkId, String policyKey, String version, String source,
                         String section, String policyType, String region, double score, double rerankScore, String text,
                         boolean injectionSuspected) {
    }

    public record Diagnostics(boolean cacheHit, boolean degraded, String mode, long latencyMs, String corpusVersion,
                              String embeddingModel, int candidates, Map<String, Object> timings) {
    }

    public record RagResponse(String answer, double confidence, boolean grounded, String policyVersion,
                              List<Source> sources, boolean insufficientEvidence, Diagnostics diagnostics) {
    }

    public record ValidateRequest(@NotBlank String orderId, String orderStatus, BigDecimal orderAmount,
                                  BigDecimal refundAmount, Integer delayDays, String shipmentStatus, String region) {
    }

    public record ValidationResult(boolean refundEligible, boolean approvalRequired, List<String> reasons,
                                   String policyId, String policyKey, String policyVersion, String citation) {
    }
}
