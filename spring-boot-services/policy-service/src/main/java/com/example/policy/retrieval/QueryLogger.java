package com.example.policy.retrieval;

import com.example.policy.PolicyRecords.RagResponse;
import com.example.policy.PolicyRecords.Source;
import com.example.policy.embedding.EmbeddingService;
import java.util.stream.Collectors;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.core.task.TaskExecutor;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/** Records one row per retrieval for quality and latency monitoring. Best effort: never slows or fails a search. */
@Component
public class QueryLogger {

    private static final Logger log = LoggerFactory.getLogger(QueryLogger.class);

    private final JdbcClient jdbc;
    private final TaskExecutor executor;

    public QueryLogger(JdbcClient jdbc, @Qualifier("retrievalExecutor") TaskExecutor executor) {
        this.jdbc = jdbc;
        this.executor = executor;
    }

    public void log(String query, long ms, boolean hit, boolean degraded, RagResponse r) {
        String ids = r.sources().stream().map(Source::chunkId).collect(Collectors.joining(",", "{", "}"));
        try {
            executor.execute(() -> {
                try {
                    jdbc.sql("""
                            INSERT INTO rag_query_log (query_hash, query_preview, latency_ms, cache_hit, degraded, confidence,
                                                       insufficient, result_chunk_ids)
                            VALUES (:h, :p, :ms, :hit, :deg, :conf, :ins, CAST(:ids AS bigint[]))""")
                            .param("h", EmbeddingService.sha256(query.toLowerCase())).param("p", query.length() > 120 ? query.substring(0, 120) : query)
                            .param("ms", (int) ms).param("hit", hit).param("deg", degraded).param("conf", r.confidence())
                            .param("ins", r.insufficientEvidence()).param("ids", ids).update();
                } catch (RuntimeException e) {
                    log.debug("query log insert failed: {}", e.getMessage());
                }
            });
        } catch (RuntimeException e) {
            log.debug("query log skipped: {}", e.getMessage());
        }
    }
}
