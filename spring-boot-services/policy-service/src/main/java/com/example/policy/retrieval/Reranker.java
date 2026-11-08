package com.example.policy.retrieval;

import java.util.List;

/** Second-stage ranking over the fused candidates. Swap the implementation to use a cross-encoder service. */
public interface Reranker {

    record Scored(Candidate candidate, double fusedScore, double rerankScore) {
    }

    List<Scored> rerank(String query, List<String> expansion, List<RankFusion.Fused> candidates, boolean vectorAvailable);
}
