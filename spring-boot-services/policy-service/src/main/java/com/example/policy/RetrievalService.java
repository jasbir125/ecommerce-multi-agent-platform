package com.example.policy;

import com.example.policy.PolicyRecords.*;
import java.time.LocalDate;
import java.util.*;
import java.util.regex.Pattern;
import java.util.stream.Collectors;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;

/**
 * Hybrid retrieval: pgvector cosine search + Postgres full-text search, merged
 * with reciprocal-rank fusion, then reranked by query-term coverage. Only the
 * latest policy version that is in force on the requested date is searched.
 * If confidence is below the threshold we say so instead of guessing.
 */
@Service
public class RetrievalService {

    static final String INSUFFICIENT = "I could not verify this from the available policy information. Please contact support.";
    private static final Pattern WORD = Pattern.compile("[a-z0-9]{3,}");
    private static final Set<String> STOP = Set.of("the", "and", "for", "are", "was", "with", "that", "this", "from",
            "have", "has", "not", "you", "your", "can", "will", "what", "when", "how", "does", "about", "please");

    private final JdbcClient jdbc;
    private final EmbeddingService embeddings;
    private final int candidates;
    private final double minConfidence;
    private final int maxContextChars;

    public RetrievalService(JdbcClient jdbc, EmbeddingService embeddings,
                            @Value("${policy.retrieval.candidates}") int candidates,
                            @Value("${policy.retrieval.min-confidence}") double minConfidence,
                            @Value("${policy.retrieval.max-context-chars}") int maxContextChars) {
        this.jdbc = jdbc;
        this.embeddings = embeddings;
        this.candidates = candidates;
        this.minConfidence = minConfidence;
        this.maxContextChars = maxContextChars;
    }

    private record Hit(long chunkId, String policyId, String policyKey, String version, String title,
                       String content, boolean injection, double sim) {
    }

    public RagResponse search(SearchRequest req) {
        LocalDate asOf = req.asOfDate() != null ? req.asOfDate() : LocalDate.now();
        int topK = req.topK() == null ? 3 : Math.max(1, Math.min(req.topK(), 5));
        List<String> terms = terms(req.query());

        Map<Long, Hit> byId = new LinkedHashMap<>();
        Map<Long, Double> fused = new HashMap<>();
        List<Hit> vectorHits = vectorSearch(req, asOf);
        for (int rank = 0; rank < vectorHits.size(); rank++) {
            Hit h = vectorHits.get(rank);
            byId.putIfAbsent(h.chunkId(), h);
            fused.merge(h.chunkId(), 1.0 / (60 + rank + 1), Double::sum);   // reciprocal-rank fusion
        }
        List<Hit> keywordHits = terms.isEmpty() ? List.of() : keywordSearch(req, asOf, terms);
        for (int rank = 0; rank < keywordHits.size(); rank++) {
            Hit h = keywordHits.get(rank);
            byId.putIfAbsent(h.chunkId(), h);
            fused.merge(h.chunkId(), 1.0 / (60 + rank + 1), Double::sum);
        }
        if (byId.isEmpty()) {
            return insufficient();
        }

        double maxFused = fused.values().stream().mapToDouble(d -> d).max().orElse(1);
        record Scored(Hit hit, double score, double rerank) {
        }
        List<Scored> ranked = byId.values().stream().map(h -> {
            double fusedNorm = fused.get(h.chunkId()) / maxFused;
            double coverage = coverage(terms, h.content());
            double rerank = 0.6 * coverage + 0.25 * fusedNorm + 0.15 * Math.max(0, h.sim());
            return new Scored(h, fused.get(h.chunkId()), rerank);
        }).sorted(Comparator.comparingDouble(Scored::rerank).reversed()).limit(topK).toList();

        double confidence = Math.min(1.0, ranked.get(0).rerank());
        if (confidence < minConfidence) {
            return insufficient();
        }

        // Build the answer only from trusted (non-flagged) chunks, within the context budget.
        StringBuilder answer = new StringBuilder();
        List<Source> sources = new ArrayList<>();
        int n = 0;
        for (Scored s : ranked) {
            Hit h = s.hit();
            sources.add(new Source(h.policyId(), h.policyKey(), h.version(), h.title(),
                    round(s.score() * 30), round(s.rerank()), snippet(h.content()), h.injection()));
            if (!h.injection() && answer.length() < maxContextChars) {
                answer.append('[').append(++n).append("] ").append(bestSentences(terms, h.content())).append(' ');
            }
        }
        if (answer.length() == 0) {
            return insufficient();   // everything retrieved was flagged as suspicious
        }
        Hit top = ranked.get(0).hit();
        return new RagResponse(answer.toString().trim(), round(confidence), true, top.version(), sources, false);
    }

    private List<Hit> vectorSearch(SearchRequest req, LocalDate asOf) {
        String q = EmbeddingService.toLiteral(embeddings.embed(req.query()));
        return jdbc.sql(ACTIVE_DOCS + """
                SELECT c.id, d.id AS doc_id, d.policy_key, d.version, d.title, c.content,
                       (c.metadata->>'injectionSuspected')::boolean AS injection,
                       1 - (c.embedding <=> CAST(:q AS vector)) AS sim
                FROM policy_chunk c JOIN active d ON d.id = c.policy_document_id
                WHERE c.metadata->>'embedder' = :emb
                  AND (CAST(:ptype AS text) IS NULL OR c.metadata->>'policyType' = :ptype)
                ORDER BY c.embedding <=> CAST(:q AS vector) LIMIT :lim""")
                .param("asof", asOf).param("region", region(req)).param("q", q).param("emb", embeddings.provider())
                .param("ptype", nullIfBlank(req.policyType())).param("lim", candidates)
                .query((rs, i) -> new Hit(rs.getLong("id"), String.valueOf(rs.getLong("doc_id")), rs.getString("policy_key"),
                        rs.getString("version"), rs.getString("title"), rs.getString("content"),
                        rs.getBoolean("injection"), rs.getDouble("sim"))).list();
    }

    private List<Hit> keywordSearch(SearchRequest req, LocalDate asOf, List<String> terms) {
        String tsq = String.join(" | ", terms);   // OR semantics; terms are [a-z0-9] only
        return jdbc.sql(ACTIVE_DOCS + """
                SELECT c.id, d.id AS doc_id, d.policy_key, d.version, d.title, c.content,
                       (c.metadata->>'injectionSuspected')::boolean AS injection, 0.0 AS sim
                FROM policy_chunk c JOIN active d ON d.id = c.policy_document_id
                WHERE c.content_tsv @@ to_tsquery('english', :tsq)
                  AND (CAST(:ptype AS text) IS NULL OR c.metadata->>'policyType' = :ptype)
                ORDER BY ts_rank(c.content_tsv, to_tsquery('english', :tsq)) DESC LIMIT :lim""")
                .param("asof", asOf).param("region", region(req)).param("tsq", tsq)
                .param("ptype", nullIfBlank(req.policyType())).param("lim", candidates)
                .query((rs, i) -> new Hit(rs.getLong("id"), String.valueOf(rs.getLong("doc_id")), rs.getString("policy_key"),
                        rs.getString("version"), rs.getString("title"), rs.getString("content"),
                        rs.getBoolean("injection"), rs.getDouble("sim"))).list();
    }

    /** Latest in-force version per policy key for the region/date. */
    static final String ACTIVE_DOCS = """
            WITH active AS (
              SELECT DISTINCT ON (policy_key) id, policy_key, version, title
              FROM policy_document
              WHERE (effective_from IS NULL OR effective_from <= :asof)
                AND (effective_to IS NULL OR effective_to >= :asof)
                AND (region = 'ALL' OR region = :region)
              ORDER BY policy_key, effective_from DESC NULLS LAST, id DESC)
            """;

    private static String region(SearchRequest req) {
        return req.region() == null || req.region().isBlank() ? "ALL" : req.region().toUpperCase();
    }

    private static String nullIfBlank(String s) {
        return s == null || s.isBlank() ? null : s;
    }

    static List<String> terms(String text) {
        var m = WORD.matcher(text.toLowerCase());
        Set<String> out = new LinkedHashSet<>();
        while (m.find()) {
            if (!STOP.contains(m.group())) {
                out.add(m.group());
            }
        }
        return new ArrayList<>(out);
    }

    private static double coverage(List<String> terms, String content) {
        if (terms.isEmpty()) {
            return 0;
        }
        String lower = content.toLowerCase();
        long hit = terms.stream().filter(t -> lower.contains(t.length() > 5 ? t.substring(0, t.length() - 1) : t)).count();
        return (double) hit / terms.size();
    }

    private static String bestSentences(List<String> terms, String content) {
        List<String> sentences = Arrays.stream(content.split("(?<=[.!?])\\s+")).collect(Collectors.toList());
        sentences.sort(Comparator.comparingDouble((String s) -> coverage(terms, s)).reversed());
        return sentences.stream().limit(2).collect(Collectors.joining(" "));
    }

    private static String snippet(String s) {
        return s.length() <= 240 ? s : s.substring(0, 240) + "...";
    }

    private static double round(double d) {
        return Math.round(d * 1000.0) / 1000.0;
    }

    private RagResponse insufficient() {
        return new RagResponse(INSUFFICIENT, 0.2, false, null, List.of(), true);
    }
}
