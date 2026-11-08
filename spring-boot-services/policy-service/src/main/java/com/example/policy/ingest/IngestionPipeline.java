package com.example.policy.ingest;

import com.example.common.audit.AuditClient;
import com.example.common.web.ApiException;
import com.example.policy.PolicyRecords.IngestRequest;
import com.example.policy.cache.SafeCache;
import com.example.policy.embedding.EmbeddingService;
import com.example.policy.ingest.PolicyStore.ChunkRow;
import com.example.policy.text.InjectionScanner;
import com.example.policy.text.Tokens;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.MeterRegistry;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

/**
 * extract -> clean -> hash -> chunk -> (reuse | batch-embed) -> store -> invalidate caches.
 *
 * - Dedup: same (key, version, text) is reported UNCHANGED; same version with different text is refused,
 *   because a published version must never change silently (publish a new version instead).
 * - Incremental: a chunk whose text already exists with the same embedding model reuses that vector, so a new
 *   version that edits one section only pays to embed that section.
 * - Atomic: nothing becomes searchable until every chunk is stored.
 */
@Component
public class IngestionPipeline {

    private final PolicyStore store;
    private final EmbeddingService embeddings;
    private final SemanticChunker.Config chunkerConfig;
    private final ObjectMapper mapper;
    private final AuditClient audit;
    private final SafeCache cache;
    private final MeterRegistry metrics;

    public IngestionPipeline(PolicyStore store, EmbeddingService embeddings, SemanticChunker.Config chunkerConfig,
                             ObjectMapper mapper, AuditClient audit, SafeCache cache, MeterRegistry metrics) {
        this.store = store;
        this.embeddings = embeddings;
        this.chunkerConfig = chunkerConfig;
        this.mapper = mapper;
        this.audit = audit;
        this.cache = cache;
        this.metrics = metrics;
    }

    public record Result(String status, long documentId, Map<String, Object> stats) {
    }

    public Result run(IngestRequest req) {
        long t0 = System.nanoTime();
        var blocks = TextExtractor.extract(req.content(), req.contentType());
        blocks = blocks.stream().map(b -> new TextExtractor.Block(b.heading() == null ? null : TextCleaner.clean(b.heading()),
                TextCleaner.clean(b.body()))).filter(b -> !b.body().isBlank()).toList();
        if (blocks.isEmpty()) {
            throw new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "EMPTY_DOCUMENT", "No readable text found in the document");
        }
        String docHash = EmbeddingService.sha256(String.join("\n", blocks.stream()
                .map(b -> (b.heading() == null ? "" : b.heading()) + "\n" + b.body()).toList()));

        var existing = store.find(req.policyKey(), req.version());
        if (existing.isPresent() && "ACTIVE".equals(existing.get().status())) {
            if (docHash.equals(existing.get().contentHash())) {
                return new Result("UNCHANGED", existing.get().id(), Map.of("reason", "identical content already ingested"));
            }
            throw new ApiException(HttpStatus.CONFLICT, "POLICY_VERSION_EXISTS", "Version " + req.version() + " of "
                    + req.policyKey() + " already exists with different content; publish a new version");
        }

        var chunks = new SemanticChunker(chunkerConfig, embeddings::embedAll).chunk(blocks);
        // De-duplicate identical chunks (can happen with overlap) while keeping order.
        Map<String, SemanticChunker.Chunk> byHash = new LinkedHashMap<>();
        for (var c : chunks) {
            byHash.putIfAbsent(EmbeddingService.sha256(c.content()), c);
        }
        String model = embeddings.modelId();
        Map<String, String> reused = store.reusableEmbeddings(byHash.keySet(), model);
        List<String> toEmbed = new ArrayList<>();
        List<String> toEmbedHashes = new ArrayList<>();
        byHash.forEach((h, c) -> {
            if (!reused.containsKey(h)) {
                toEmbedHashes.add(h);
                toEmbed.add(c.content());
            }
        });
        Map<String, String> vectors = new LinkedHashMap<>(reused);
        if (!toEmbed.isEmpty()) {
            List<float[]> fresh = embeddings.embedAll(toEmbed);            // batched, cached, retried
            for (int i = 0; i < toEmbed.size(); i++) {
                vectors.put(toEmbedHashes.get(i), EmbeddingService.literal(fresh.get(i)));
            }
        }

        List<ChunkRow> rows = new ArrayList<>();
        int idx = 0, flagged = 0, tokens = 0;
        for (var e : byHash.entrySet()) {
            var c = e.getValue();
            boolean suspicious = InjectionScanner.suspicious(c.content());
            flagged += suspicious ? 1 : 0;
            tokens += c.tokens();
            rows.add(new ChunkRow(idx++, c.section(), c.content(), e.getKey(), c.tokens(),
                    metadata(req, c, suspicious), vectors.get(e.getKey())));
        }
        long docId = store.save(req, docHash, rows, model);
        cache.bumpCorpusVersion();                                          // all cached answers are now stale
        if (flagged > 0) {
            audit.record(null, "policy-service", "PROMPT_INJECTION_SUSPECTED_IN_POLICY", null,
                    Map.of("policyKey", req.policyKey(), "version", req.version(), "chunks", flagged));
        }
        metrics.counter("rag.ingest.documents").increment();
        Map<String, Object> stats = new LinkedHashMap<>();
        stats.put("chunks", rows.size());
        stats.put("chunksReused", reused.size());
        stats.put("chunksEmbedded", toEmbed.size());
        stats.put("tokens", tokens);
        stats.put("injectionFlaggedChunks", flagged);
        stats.put("embeddingModel", model);
        stats.put("durationMs", (System.nanoTime() - t0) / 1_000_000);
        return new Result("DONE", docId, stats);
    }

    private String metadata(IngestRequest req, SemanticChunker.Chunk c, boolean suspicious) {
        Set<String> keywords = new LinkedHashSet<>(Tokens.terms(c.content()).stream().limit(40).toList());
        try {
            return mapper.writeValueAsString(Map.of("policyKey", req.policyKey(), "version", req.version(),
                    "section", c.section() == null ? "" : c.section(), "keywords", keywords.stream().limit(12).toList(),
                    "injectionSuspected", suspicious));
        } catch (JsonProcessingException e) {
            throw new IllegalStateException(e);
        }
    }
}
