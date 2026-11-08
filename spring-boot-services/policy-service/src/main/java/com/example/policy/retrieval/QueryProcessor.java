package com.example.policy.retrieval;

import com.example.policy.text.Tokens;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.springframework.stereotype.Component;

/**
 * Query normalisation and expansion. Expansion adds synonyms (from configuration) to the KEYWORD search only;
 * the embedding is computed from the user's own wording.
 */
@Component
public class QueryProcessor {

    static final int MAX_QUERY_CHARS = 500;

    /** Default synonyms; extend with policy.retrieval.synonyms in application.yml if needed. */
    private static final Map<String, List<String>> SYNONYMS = Map.ofEntries(
            Map.entry("delayed", List.of("late", "overdue", "delay")),
            Map.entry("late", List.of("delayed", "delay")),
            Map.entry("delay", List.of("late", "delayed")),
            Map.entry("lost", List.of("missing", "no scan")),
            Map.entry("missing", List.of("lost")),
            Map.entry("refund", List.of("reimbursement", "money back")),
            Map.entry("cancel", List.of("cancellation", "cancelled")),
            Map.entry("replacement", List.of("replace", "substitute", "exchange")),
            Map.entry("replace", List.of("replacement", "substitute")),
            Map.entry("return", List.of("send back", "returns")),
            Map.entry("damaged", List.of("defective", "broken")),
            Map.entry("defective", List.of("damaged", "faulty")),
            Map.entry("warranty", List.of("guarantee", "repair")),
            Map.entry("cheaper", List.of("lower price", "same or less")));

    public String normalize(String raw) {
        String q = Normalizer.normalize(raw == null ? "" : raw, Normalizer.Form.NFKC)
                .replaceAll("[\\p{Cntrl}]", " ").replaceAll("\\s+", " ").strip();
        return q.length() > MAX_QUERY_CHARS ? q.substring(0, MAX_QUERY_CHARS) : q;
    }

    /** Terms from the query itself. */
    public List<String> baseTerms(String query) {
        return new ArrayList<>(new LinkedHashSet<>(Tokens.terms(query)));
    }

    /** Synonym terms that are not already in the query (single words only; phrases are skipped for tsquery safety). */
    public List<String> expansionTerms(String query) {
        Set<String> base = new LinkedHashSet<>(baseTerms(query));
        Set<String> out = new LinkedHashSet<>();
        for (String t : base) {
            for (String syn : SYNONYMS.getOrDefault(t, List.of())) {
                if (!syn.contains(" ") && !base.contains(syn)) {
                    out.add(syn);
                }
            }
        }
        return new ArrayList<>(out);
    }

    /** OR-query for to_tsquery. Only [a-z0-9] terms reach it. */
    public String tsQuery(String query) {
        List<String> all = new ArrayList<>(baseTerms(query));
        all.addAll(expansionTerms(query));
        return String.join(" | ", all.stream().filter(t -> t.matches("[a-z0-9]+")).toList());
    }
}
