package com.example.policy.retrieval;

import static org.assertj.core.api.Assertions.assertThat;

import com.example.policy.retrieval.Reranker.Scored;
import java.time.LocalDate;
import java.util.List;
import org.junit.jupiter.api.Test;

class RegionPrecedenceTest {

    private static Scored s(long id, String region, String type, double score) {
        return new Scored(new Candidate(id, id, "k" + id, "1", "t", type, null, region, "ALL", "s", "c", false, 0.5), 1, score);
    }

    private RetrievalService service() {
        return new RetrievalService(null, null, null, null, null, null, null, null, new io.micrometer.core.instrument.simple.SimpleMeterRegistry(),
                Runnable::run, 20, 3, 450, 4000, 0.35, 600, 120, "test");
    }

    @Test
    void regionalPolicyOverridesGlobalPolicyOfTheSameType() {
        var f = Filters.of(LocalDate.now(), "EU", null, null, null, null, null);
        var out = service().applyRegionPrecedence(List.of(s(1, "ALL", "returns", 0.70), s(2, "EU", "returns", 0.50), s(3, "ALL", "warranty", 0.40)), f);
        assertThat(out.get(0).candidate().chunkId()).isEqualTo(2);              // EU rule now first
        assertThat(out.stream().filter(x -> x.candidate().chunkId() == 1).findFirst().get().rerankScore()).isEqualTo(0.49, org.assertj.core.data.Offset.offset(0.001));
        assertThat(out.get(0).rerankScore()).isEqualTo(0.65, org.assertj.core.data.Offset.offset(0.001));          // regional chunk boosted
        assertThat(out.stream().filter(x -> x.candidate().chunkId() == 3).findFirst().get().rerankScore()).isEqualTo(0.40);   // other types untouched
    }

    @Test
    void anIrrelevantRegionalChunkDoesNotDemoteGlobalOnes() {
        var f = Filters.of(LocalDate.now(), "EU", null, null, null, null, null);
        var out = service().applyRegionPrecedence(List.of(s(1, "ALL", "returns", 0.70), s(2, "EU", "returns", 0.10)), f);
        assertThat(out.get(0).candidate().chunkId()).isEqualTo(1);
    }

    @Test
    void noRegionMeansNoPrecedenceRules() {
        var f = Filters.of(LocalDate.now(), null, null, null, null, null, null);
        var in = List.of(s(1, "ALL", "returns", 0.70), s(2, "EU", "returns", 0.50));
        assertThat(service().applyRegionPrecedence(in, f)).isEqualTo(in);
    }
}
