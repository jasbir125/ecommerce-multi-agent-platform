package com.example.policy.retrieval;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.Test;

class RankFusionTest {

    static Candidate c(long id, double sim) {
        return new Candidate(id, 1, "k", "1", "t", "refund", null, "ALL", "ALL", "s", "content " + id, false, sim);
    }

    @Test
    void itemsFoundByBothBranchesRankFirstAndScoresAreNormalised() {
        var fused = RankFusion.fuse(List.of(c(1, 0.9), c(2, 0.8)), List.of(c(2, 0), c(3, 0)), 1.0, 0.8);
        assertThat(fused.get(0).candidate().chunkId()).isEqualTo(2);
        assertThat(fused.get(0).score()).isEqualTo(1.0);
        assertThat(fused).extracting(f -> f.candidate().chunkId()).containsExactlyInAnyOrder(1L, 2L, 3L);
    }

    @Test
    void vectorSimilaritySurvivesTheMerge() {
        var fused = RankFusion.fuse(List.of(c(2, 0.77)), List.of(c(2, 0)), 1.0, 0.8);
        assertThat(fused.get(0).candidate().sim()).isEqualTo(0.77);
    }

    @Test
    void emptyInputsGiveNothing() {
        assertThat(RankFusion.fuse(List.of(), List.of(), 1, 1)).isEmpty();
    }
}
