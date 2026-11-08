package com.example.policy.retrieval;

import java.time.LocalDate;

/** A chunk found by vector or keyword search, with the document facts needed to cite it. */
public record Candidate(long chunkId, long docId, String policyKey, String version, String title, String policyType,
                        LocalDate effectiveFrom, String region, String category, String section, String content,
                        boolean injection, double sim) {

    public Candidate withSim(double s) {
        return new Candidate(chunkId, docId, policyKey, version, title, policyType, effectiveFrom, region, category, section, content, injection, s);
    }
}
