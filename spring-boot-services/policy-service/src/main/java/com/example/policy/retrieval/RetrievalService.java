package com.example.policy.retrieval;

import com.example.common.web.ApiException;
import com.example.policy.PolicyRecords.*;
import com.example.policy.cache.SafeCache;
import com.example.policy.embedding.Embedder;
import com.example.policy.embedding.EmbeddingService;
import com.example.policy.retrieval.Reranker.Scored;
import com.example.policy.text.Tokens;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.MeterRegistry;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.task.TaskExecutor;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

/**
 * The retrieval pipeline:
 *   normalise -> cache lookup -> [embed + vector search || keyword search with synonyms] (parallel, with timeouts)
 *   -> rank fusion -> de-duplication -> rerank -> diversity cap -> confidence gate -> context compression -> cache.
 *
 * Failure behaviour: if the embedding provider or vector search fails, the answer is built from keyword
 * search alone and flagged degraded (with lower confidence). If nothing is found or confidence is below the
 * threshold the answer is "insufficient evidence"; the service never guesses.
 */
@Service
public class RetrievalService {

    public static final String INSUFFICIENT = "I could not verify this from the available policy information. Please contact support.";
    private static final Logger log = LoggerFactory.getLogger(RetrievalService.class);
    private static final Set<String> MODES = Set.of("hybrid", "vector", "keyword");

    private final QueryProcessor processor;
    private final CandidateSource searcher;
    private final Embedder embeddings;
    private final Reranker reranker;
    private final ContextCompressor compressor;
    private final SafeCache cache;
    private final QueryLogger queryLog;
    private final ObjectMapper mapper;
    private final MeterRegistry metrics;
    private final TaskExecutor executor;
    private final String rankingVersion;
    private final int candidates, maxPerDoc, maxContextTokens, timeoutMs;
    private final double minConfidence;
    private final Duration ttlHit, ttlMiss;

    public RetrievalService(QueryProcessor processor, CandidateSource searcher, Embedder embeddings, Reranker reranker,
                            ContextCompressor compressor, SafeCache cache, QueryLogger queryLog, ObjectMapper mapper,
                            MeterRegistry metrics, @Qualifier("retrievalExecutor") TaskExecutor executor,
                            @Value("${policy.retrieval.candidates:20}") int candidates,
                            @Value("${policy.retrieval.max-chunks-per-document:3}") int maxPerDoc,
                            @Value("${policy.retrieval.max-context-tokens:450}") int maxContextTokens,
                            @Value("${policy.retrieval.stage-timeout-ms:4000}") int timeoutMs,
                            @Value("${policy.retrieval.min-confidence:0.35}") double minConfidence,
                            @Value("${policy.retrieval.cache-ttl-seconds:600}") int ttlSeconds,
                            @Value("${policy.retrieval.cache-ttl-insufficient-seconds:120}") int ttlMissSeconds,
                            @Value("${policy.retrieval.ranking-version:r1}") String rankingVersion) {
        this.rankingVersion = rankingVersion;
        this.processor = processor;
        this.searcher = searcher;
        this.embeddings = embeddings;
        this.reranker = reranker;
        this.compressor = compressor;
        this.cache = cache;
        this.queryLog = queryLog;
        this.mapper = mapper;
        this.metrics = metrics;
        this.executor = executor;
        this.candidates = candidates;
        this.maxPerDoc = maxPerDoc;
        this.maxContextTokens = maxContextTokens;
        this.timeoutMs = timeoutMs;
        this.minConfidence = minConfidence;
        this.ttlHit = Duration.ofSeconds(ttlSeconds);
        this.ttlMiss = Duration.ofSeconds(ttlMissSeconds);
    }

    public RagResponse search(SearchRequest req) {
        long t0 = System.nanoTime();
        String query = processor.normalize(req.query());
        if (query.isBlank()) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "EMPTY_QUERY", "query must not be empty");
        }
        Filters f = Filters.of(req.asOfDate(), req.region(), req.policyType(), req.tenantId(), req.customerSegment(),
                req.productCategory(), req.version());
        String mode = req.mode() == null ? "hybrid" : req.mode().toLowerCase();
        if (!MODES.contains(mode)) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "INVALID_MODE", "mode must be hybrid, vector or keyword");
        }
        int topK = req.topK() == null ? 4 : Math.max(1, Math.min(req.topK(), 8));
        String corpus = cache.corpusVersion();
        String key = "rag:q:" + EmbeddingService.sha256(String.join("|", query.toLowerCase(), f.cacheKey(), mode,
                String.valueOf(topK), corpus, embeddings.modelId(), String.valueOf(maxContextTokens), rankingVersion));

        Optional<RagResponse> cached = cache.get(key).flatMap(this::read);
        if (cached.isPresent()) {
            RagResponse r = cached.get();
            long ms = elapsed(t0);
            record(query, ms, true, r.diagnostics().degraded(), r);
            return withDiagnostics(r, new Diagnostics(true, r.diagnostics().degraded(), mode, ms, corpus, embeddings.modelId(),
                    r.diagnostics().candidates(), r.diagnostics().timings()));
        }

        Map<String, Object> timings = new ConcurrentHashMap<>();
        boolean wantVector = !mode.equals("keyword"), wantKeyword = !mode.equals("vector");
        CompletableFuture<List<Candidate>> vecF = wantVector ? CompletableFuture.supplyAsync(() -> {
            long s = System.nanoTime();
            float[] v = embeddings.embed(query);
            timings.put("embedMs", elapsed(s));
            long s2 = System.nanoTime();
            List<Candidate> out = searcher.vector(v, embeddings.modelId(), f, candidates);
            timings.put("vectorSearchMs", elapsed(s2));
            return out;
        }, executor) : null;
        CompletableFuture<List<Candidate>> kwF = wantKeyword ? CompletableFuture.supplyAsync(() -> {
            long s = System.nanoTime();
            List<Candidate> out = searcher.keyword(processor.tsQuery(query), f, candidates);
            timings.put("keywordSearchMs", elapsed(s));
            return out;
        }, executor) : null;

        Optional<List<Candidate>> vec = vecF == null ? Optional.of(List.of()) : await(vecF, "vector");
        Optional<List<Candidate>> kw = kwF == null ? Optional.of(List.of()) : await(kwF, "keyword");
        boolean degraded = (wantVector && vec.isEmpty()) || (wantKeyword && kw.isEmpty());
        if (vec.isEmpty() && kw.isEmpty()) {
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "RETRIEVAL_UNAVAILABLE", "Policy search is temporarily unavailable");
        }
        boolean vectorUsed = wantVector && vec.isPresent();

        long tr = System.nanoTime();
        List<RankFusion.Fused> fused = RankFusion.fuse(vec.orElse(List.of()), kw.orElse(List.of()), 1.0, 0.8);
        int found = fused.size();
        List<String> expansion = processor.expansionTerms(query);
        List<Scored> ranked = applyRegionPrecedence(reranker.rerank(query, expansion, fused, vectorUsed), f);
        List<Scored> picked = select(ranked, topK);
        timings.put("rerankMs", elapsed(tr));

        double confidence = picked.isEmpty() ? 0 : Math.min(1.0, picked.get(0).rerankScore()) * (degraded ? 0.9 : 1.0);
        timings.put("rawConfidence", round(confidence));
        RagResponse response;
        if (picked.isEmpty() || confidence < minConfidence) {
            response = new RagResponse(INSUFFICIENT, round(Math.min(confidence, 0.2)), false, null, List.of(), true,
                    diag(false, degraded, mode, t0, corpus, found, timings, req));
        } else {
            response = build(picked, confidence, query, expansion, degraded, mode, t0, corpus, found, timings, req);
        }
        cache.set(key, write(response), response.insufficientEvidence() ? ttlMiss : ttlHit);
        record(query, elapsed(t0), false, degraded, response);
        return response;
    }

    private RagResponse build(List<Scored> picked, double confidence, String query, List<String> expansion, boolean degraded,
                              String mode, long t0, String corpus, int found, Map<String, Object> timings, SearchRequest req) {
        Set<String> stems = new LinkedHashSet<>(Tokens.stems(query));
        expansion.forEach(e -> stems.add(Tokens.stem(e)));
        int perSource = Math.max(60, maxContextTokens / picked.size());
        List<Source> sources = new ArrayList<>();
        StringBuilder answer = new StringBuilder();
        int n = 0;
        for (Scored s : picked) {
            Candidate c = s.candidate();
            String text = c.injection() ? null : compressor.compress(c.content(), stems, perSource);   // flagged text is never returned
            sources.add(new Source(String.valueOf(c.docId()), String.valueOf(c.chunkId()), c.policyKey(), c.version(), c.title(),
                    c.section(), c.policyType(), c.region(), round(s.fusedScore()), round(s.rerankScore()), text, c.injection()));
            if (text != null) {
                answer.append('[').append(++n).append("] ").append(text).append(' ');
            }
        }
        if (n == 0) {
            return new RagResponse(INSUFFICIENT, 0.2, false, null, List.of(), true, diag(false, degraded, mode, t0, corpus, found, timings, req));
        }
        String version = picked.get(0).candidate().version();
        return new RagResponse(answer.toString().strip(), round(confidence), true, version, sources, false,
                diag(false, degraded, mode, t0, corpus, found, timings, req));
    }

    static final double REGION_PRECEDENCE_FLOOR = 0.25, GLOBAL_PENALTY = 0.7, REGIONAL_BOOST = 0.15;

    /**
     * A region-specific policy overrides the global one on the same topic: if the caller's region has a relevant
     * policy of some type (say "returns"), global chunks of that type are pushed down. Similarity alone cannot know
     * the caller is in the EU, so this is an explicit rule.
     */
    List<Scored> applyRegionPrecedence(List<Scored> ranked, Filters f) {
        if ("ALL".equals(f.region())) {
            return ranked;
        }
        Set<String> regionalTypes = new LinkedHashSet<>();
        for (Scored s : ranked) {
            if (f.region().equals(s.candidate().region()) && s.rerankScore() >= REGION_PRECEDENCE_FLOOR) {
                regionalTypes.add(s.candidate().policyType());
            }
        }
        if (regionalTypes.isEmpty()) {
            return ranked;
        }
        return ranked.stream().map(s -> {
                    if (!regionalTypes.contains(s.candidate().policyType())) {
                        return s;
                    }
                    if ("ALL".equals(s.candidate().region())) {
                        return new Scored(s.candidate(), s.fusedScore(), s.rerankScore() * GLOBAL_PENALTY);
                    }
                    return f.region().equals(s.candidate().region())
                            ? new Scored(s.candidate(), s.fusedScore(), Math.min(1, s.rerankScore() + REGIONAL_BOOST)) : s;
                }).sorted((a, b) -> Double.compare(b.rerankScore(), a.rerankScore())).toList();
    }

    /** Drops near-duplicates and limits how many chunks one document can contribute. */
    List<Scored> select(List<Scored> ranked, int topK) {
        List<Scored> out = new ArrayList<>();
        Map<Long, Integer> perDoc = new HashMap<>();
        List<Set<String>> seen = new ArrayList<>();
        for (Scored s : ranked) {
            Set<String> stems = Tokens.stems(s.candidate().content());
            if (seen.stream().anyMatch(o -> Tokens.jaccard(o, stems) > 0.85)) {
                continue;
            }
            if (perDoc.merge(s.candidate().docId(), 1, Integer::sum) > maxPerDoc) {
                continue;
            }
            seen.add(stems);
            out.add(s);
            if (out.size() == topK) {
                break;
            }
        }
        return out;
    }

    private <T> Optional<T> await(CompletableFuture<T> f, String what) {
        try {
            return Optional.ofNullable(f.get(timeoutMs, TimeUnit.MILLISECONDS));
        } catch (Exception e) {
            if (e instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            f.cancel(true);
            log.warn("{} retrieval branch failed, continuing without it: {}", what, rootMessage(e));
            metrics.counter("rag.retrieval.branch.failures", "branch", what).increment();
            return Optional.empty();
        }
    }

    private static String rootMessage(Throwable e) {
        while (e.getCause() != null) {
            e = e.getCause();
        }
        return e.getClass().getSimpleName() + ": " + e.getMessage();
    }

    private Diagnostics diag(boolean hit, boolean degraded, String mode, long t0, String corpus, int found,
                             Map<String, Object> timings, SearchRequest req) {
        return new Diagnostics(hit, degraded, mode, elapsed(t0), corpus, embeddings.modelId(), found,
                new HashMap<>(timings));
    }

    private void record(String query, long ms, boolean hit, boolean degraded, RagResponse r) {
        metrics.timer("rag.retrieval.latency", "cache", hit ? "hit" : "miss", "degraded", String.valueOf(degraded))
                .record(Duration.ofMillis(ms));
        if (r.insufficientEvidence()) {
            metrics.counter("rag.retrieval.insufficient").increment();
        }
        queryLog.log(query, ms, hit, degraded, r);
    }

    private static RagResponse withDiagnostics(RagResponse r, Diagnostics d) {
        return new RagResponse(r.answer(), r.confidence(), r.grounded(), r.policyVersion(), r.sources(), r.insufficientEvidence(), d);
    }

    private Optional<RagResponse> read(String json) {
        try {
            return Optional.of(mapper.readValue(json, RagResponse.class));
        } catch (Exception e) {
            return Optional.empty();       // unreadable entry: treat as a miss
        }
    }

    private String write(RagResponse r) {
        try {
            return mapper.writeValueAsString(r);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    private static long elapsed(long t0) {
        return (System.nanoTime() - t0) / 1_000_000;
    }

    private static double round(double d) {
        return Math.round(d * 1000.0) / 1000.0;
    }
}
