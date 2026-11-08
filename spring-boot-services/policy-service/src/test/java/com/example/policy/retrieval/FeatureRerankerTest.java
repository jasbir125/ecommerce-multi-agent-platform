package com.example.policy.retrieval;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.Test;

class FeatureRerankerTest {

    private final FeatureReranker reranker = new FeatureReranker();

    private static RankFusion.Fused f(long id, String section, String text, double sim, double fused) {
        return new RankFusion.Fused(new Candidate(id, id, "k", "1", "t", "refund", null, "ALL", "ALL", section, text, false, sim), fused);
    }

    @Test
    void relevantChunkBeatsIrrelevantOne() {
        var out = reranker.rerank("refund for a delayed shipment", List.of("late"), List.of(
                f(1, "Warranty", "Warranty: Electronics have a twelve month limited warranty for defects.", 0.30, 0.9),
                f(2, "Delayed shipments", "Delayed shipments: orders delayed three days are eligible for a full refund.", 0.55, 0.6)), true);
        assertThat(out.get(0).candidate().chunkId()).isEqualTo(2);
        assertThat(out.get(0).rerankScore()).isGreaterThan(0.6);
        assertThat(out.get(1).rerankScore()).isLessThan(0.4);
    }

    @Test
    void anOffTopicQueryScoresLowEvenForTheBestAvailableChunk() {
        var out = reranker.rerank("warranty on quantum toasters", List.of(), List.of(
                f(1, "Delayed shipments", "Delayed shipments: orders delayed three days are eligible for a full refund.", 0.15, 1.0)), true);
        assertThat(out.get(0).rerankScore()).isLessThan(0.35);
    }

    @Test
    void synonymsHelpButCountLessThanTheUsersOwnWords() {
        var exact = reranker.rerank("delayed order", List.of("late"), List.of(f(1, null, "A delayed order is refunded.", 0, 1)), false);
        var syn = reranker.rerank("delayed order", List.of("late"), List.of(f(1, null, "A late order is refunded.", 0, 1)), false);
        assertThat(exact.get(0).rerankScore()).isGreaterThan(syn.get(0).rerankScore());
        assertThat(syn.get(0).rerankScore()).isGreaterThan(0.1);
    }

    @Test
    void worksWithoutVectorsAndStaysWithinZeroToOne() {
        var out = reranker.rerank("refund policy", List.of(), List.of(f(1, "Refund", "Refund policy text about refunds.", 0, 1)), false);
        assertThat(out.get(0).rerankScore()).isBetween(0.0, 1.0);
    }

    @Test
    void similarityScalingIsClamped() {
        assertThat(FeatureReranker.scaleSimilarity(0.1)).isZero();
        assertThat(FeatureReranker.scaleSimilarity(0.9)).isEqualTo(1.0);
        assertThat(FeatureReranker.scaleSimilarity(0.50)).isEqualTo(0.5, org.assertj.core.data.Offset.offset(0.001));
    }
}
