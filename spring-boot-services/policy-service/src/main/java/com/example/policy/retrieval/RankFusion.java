package com.example.policy.retrieval;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Reciprocal-rank fusion: merges ranked lists without needing comparable scores. */
public final class RankFusion {

    private RankFusion() {
    }

    public record Fused(Candidate candidate, double score) {
    }

    /** @return candidates ordered by fused score; score is normalised to 0..1 (best = 1). */
    public static List<Fused> fuse(List<Candidate> vector, List<Candidate> keyword, double vectorWeight, double keywordWeight) {
        Map<Long, Candidate> byId = new LinkedHashMap<>();
        Map<Long, Double> score = new HashMap<>();
        add(vector, vectorWeight, byId, score, true);
        add(keyword, keywordWeight, byId, score, false);
        double max = score.values().stream().mapToDouble(d -> d).max().orElse(1);
        return byId.values().stream()
                .map(c -> new Fused(c, score.get(c.chunkId()) / max))
                .sorted((a, b) -> Double.compare(b.score(), a.score())).toList();
    }

    private static void add(List<Candidate> list, double weight, Map<Long, Candidate> byId, Map<Long, Double> score, boolean keepSim) {
        for (int rank = 0; rank < list.size(); rank++) {
            Candidate c = list.get(rank);
            byId.merge(c.chunkId(), c, (old, neu) -> keepSim ? neu : old.withSim(Math.max(old.sim(), neu.sim())));
            score.merge(c.chunkId(), weight / (60 + rank + 1), Double::sum);
        }
    }
}
