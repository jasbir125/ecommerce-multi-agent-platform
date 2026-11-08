package com.example.policy.ingest;

import com.example.common.util.Ids;
import com.example.common.web.ApiException;
import com.example.policy.PolicyRecords.IngestJob;
import com.example.policy.PolicyRecords.IngestRequest;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.annotation.PostConstruct;
import java.util.Map;
import java.util.concurrent.RejectedExecutionException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import org.springframework.stereotype.Service;

/** Runs ingestion in the background and records progress, so a large document never blocks an HTTP request. */
@Service
public class IngestionJobService {

    private static final Logger log = LoggerFactory.getLogger(IngestionJobService.class);

    private final JdbcClient jdbc;
    private final IngestionPipeline pipeline;
    private final ThreadPoolTaskExecutor executor;
    private final ObjectMapper mapper;

    public IngestionJobService(JdbcClient jdbc, IngestionPipeline pipeline,
                               @Qualifier("ingestionExecutor") ThreadPoolTaskExecutor executor, ObjectMapper mapper) {
        this.jdbc = jdbc;
        this.pipeline = pipeline;
        this.executor = executor;
        this.mapper = mapper;
    }

    /** Jobs that were running when the process stopped will never finish: mark them failed. */
    @PostConstruct
    void failInterruptedJobs() {
        int n = jdbc.sql("UPDATE ingestion_job SET status = 'FAILED', error = 'interrupted by restart', finished_at = now() WHERE status IN ('QUEUED','RUNNING')").update();
        if (n > 0) {
            log.warn("Marked {} interrupted ingestion jobs as FAILED", n);
        }
    }

    public IngestJob submit(IngestRequest req) {
        String jobId = Ids.next("JOB");
        jdbc.sql("INSERT INTO ingestion_job (job_id, policy_key, version) VALUES (:id, :k, :v)")
                .param("id", jobId).param("k", req.policyKey()).param("v", req.version()).update();
        try {
            executor.execute(() -> execute(jobId, req));
        } catch (RejectedExecutionException e) {
            jdbc.sql("UPDATE ingestion_job SET status = 'FAILED', error = 'queue full', finished_at = now() WHERE job_id = :id").param("id", jobId).update();
            throw new ApiException(HttpStatus.TOO_MANY_REQUESTS, "INGESTION_QUEUE_FULL", "Too many ingestion jobs queued; retry later");
        }
        return get(jobId);
    }

    /** Runs the job on the calling thread (used by tests and the startup seeder). */
    public IngestJob runNow(IngestRequest req) {
        String jobId = Ids.next("JOB");
        jdbc.sql("INSERT INTO ingestion_job (job_id, policy_key, version) VALUES (:id, :k, :v)")
                .param("id", jobId).param("k", req.policyKey()).param("v", req.version()).update();
        execute(jobId, req);
        return get(jobId);
    }

    private void execute(String jobId, IngestRequest req) {
        jdbc.sql("UPDATE ingestion_job SET status = 'RUNNING' WHERE job_id = :id").param("id", jobId).update();
        try {
            var result = pipeline.run(req);
            jdbc.sql("""
                    UPDATE ingestion_job SET status = :s, stats = CAST(:st AS jsonb), document_id = :d, finished_at = now()
                    WHERE job_id = :id""").param("s", result.status()).param("st", mapper.writeValueAsString(result.stats()))
                    .param("d", result.documentId()).param("id", jobId).update();
        } catch (Exception e) {
            log.warn("Ingestion {} failed: {}", jobId, e.getMessage());
            jdbc.sql("UPDATE ingestion_job SET status = 'FAILED', error = :e, finished_at = now() WHERE job_id = :id")
                    .param("e", String.valueOf(e.getMessage())).param("id", jobId).update();
        }
    }

    public IngestJob get(String jobId) {
        return jdbc.sql("SELECT job_id, status, policy_key, version, stats::text AS stats, error, document_id FROM ingestion_job WHERE job_id = :id")
                .param("id", jobId).query((rs, i) -> new IngestJob(rs.getString("job_id"), rs.getString("status"),
                        rs.getString("policy_key"), rs.getString("version"), parse(rs.getString("stats")), rs.getString("error"),
                        (Long) rs.getObject("document_id"))).optional()
                .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "JOB_NOT_FOUND", "Unknown ingestion job"));
    }

    private Map<String, Object> parse(String json) {
        if (json == null) {
            return Map.of();
        }
        try {
            return mapper.readValue(json, new TypeReference<>() { });
        } catch (JsonProcessingException e) {
            return Map.of();
        }
    }
}
