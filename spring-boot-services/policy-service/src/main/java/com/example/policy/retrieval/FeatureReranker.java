package com.example.policy.retrieval;

import com.example.policy.text.Tokens;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import org.springframework.stereotype.Component;

/**
 * Scores each candidate from several signals that are mostly absolute (so a weak best match stays weak):
 * query-term coverage (synonyms count less), BM25 over the candidate set, phrase matches, heading match,
 * vector similarity and the fused rank. If the vector side is unavailable its weight moves to coverage.
 * Output is 0..1.
 */
@Component
public class FeatureReranker implements Reranker {

    // With embeddings the semantic score leads (it separates answerable from unanswerable questions best);
    // lexical features support it. Without embeddings (degraded mode) the lexical features carry the score.
    static final double V_SEMANTIC = 0.55, V_COVERAGE = 0.18, V_BM25 = 0.12, V_PHRASE = 0.05, V_HEADING = 0.05, V_FUSED = 0.05;
    static final double K_COVERAGE = 0.40, K_BM25 = 0.30, K_PHRASE = 0.10, K_HEADING = 0.10, K_FUSED = 0.10;

    @Override
    public List<Scored> rerank(String query, List<String> expansion, List<RankFusion.Fused> candidates, boolean vectorAvailable) {
        List<String> qTerms = Tokens.terms(query);
        Set<String> qStems = qTerms.stream().map(Tokens::stem).collect(Collectors.toCollection(java.util.LinkedHashSet::new));
        Set<String> synStems = expansion.stream().map(Tokens::stem).filter(s -> !qStems.contains(s)).collect(Collectors.toSet());
        List<String> qBigrams = Tokens.bigrams(qTerms.stream().map(Tokens::stem).toList());

        List<Set<String>> docStems = candidates.stream().map(f -> Tokens.stems(f.candidate().content())).toList();
        List<List<String>> docTokens = candidates.stream()
                .map(f -> Tokens.terms(f.candidate().content()).stream().map(Tokens::stem).toList()).toList();
        Map<String, Integer> df = new HashMap<>();
        for (Set<String> s : docStems) {
            for (String t : s) {
                df.merge(t, 1, Integer::sum);
            }
        }
        double avgLen = docTokens.stream().mapToInt(List::size).average().orElse(1);

        double[] bm25 = new double[candidates.size()];
        double maxBm = 1e-9;
        for (int i = 0; i < candidates.size(); i++) {
            Map<String, Long> tf = docTokens.get(i).stream().collect(Collectors.groupingBy(t -> t, Collectors.counting()));
            for (String t : qStems) {
                bm25[i] += bm25Term(tf.getOrDefault(t, 0L), df.getOrDefault(t, 0), candidates.size(), docTokens.get(i).size(), avgLen) * 1.0;
            }
            for (String t : synStems) {
                bm25[i] += bm25Term(tf.getOrDefault(t, 0L), df.getOrDefault(t, 0), candidates.size(), docTokens.get(i).size(), avgLen) * 0.5;
            }
            maxBm = Math.max(maxBm, bm25[i]);
        }

        List<Scored> out = new ArrayList<>();
        for (int i = 0; i < candidates.size(); i++) {
            Candidate c = candidates.get(i).candidate();
            Set<String> stems = docStems.get(i);
            double coverage = qStems.isEmpty() ? 0 : (qStems.stream().filter(stems::contains).count()
                    + 0.5 * synStems.stream().filter(stems::contains).count() / Math.max(1, synStems.size()) * Math.min(1, synStems.size())) / qStems.size();
            coverage = Math.min(1, coverage);
            String lower = " " + String.join(" ", docTokens.get(i)) + " ";
            double phrase = qBigrams.isEmpty() ? 0 : (double) qBigrams.stream().filter(b -> lower.contains(" " + b + " ")).count() / qBigrams.size();
            double heading = c.section() == null || qStems.isEmpty() ? 0
                    : (double) Tokens.stems(c.section()).stream().filter(qStems::contains).count() / Math.max(1, Tokens.stems(c.section()).size());
            double bm = (bm25[i] / maxBm) * Math.min(1, coverage * 1.5);
            double score = vectorAvailable
                    ? V_SEMANTIC * scaleSimilarity(c.sim()) + V_COVERAGE * coverage + V_BM25 * bm + V_PHRASE * phrase
                            + V_HEADING * heading + V_FUSED * candidates.get(i).score()
                    : K_COVERAGE * coverage + K_BM25 * bm + K_PHRASE * phrase + K_HEADING * heading + K_FUSED * candidates.get(i).score();
            out.add(new Scored(c, candidates.get(i).score(), Math.max(0, Math.min(1, score))));
        }
        out.sort(Comparator.comparingDouble(Scored::rerankScore).reversed());
        return out;
    }

    /**
     * Calibrated on the golden set: relevant chunks score >= 0.42 (median 0.63) and unanswerable questions
     * top out at 0.42 (median 0.30). Map 0.30..0.70 onto 0..1.
     */
    static double scaleSimilarity(double sim) {
        return Math.max(0, Math.min(1, (sim - 0.30) / 0.40));
    }

    private static double bm25Term(long tf, int df, int n, int len, double avgLen) {
        if (tf == 0) {
            return 0;
        }
        double idf = Math.log(1 + (n - df + 0.5) / (df + 0.5));
        double k1 = 1.2, b = 0.75;
        return idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * len / avgLen));
    }
}
