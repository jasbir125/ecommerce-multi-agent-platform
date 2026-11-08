package com.example.policy;

import com.example.common.audit.AuditClient;
import com.example.policy.PolicyRecords.*;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.example.common.web.ApiException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.regex.Pattern;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Chunks, embeds and stores a policy. De-duplicates by content hash, refuses to
 * overwrite a published (key, version) with different text, and flags text that
 * looks like a prompt-injection attempt (still stored, but never used in answers).
 */
@Service
public class IngestionService {

    static final Pattern INJECTION = Pattern.compile(
            "ignore (all )?(the )?(previous|prior|above) instructions|disregard .{0,40}instructions"
                    + "|you (must|should) (now )?(issue|approve|process) (a )?refund|system prompt|act as (an? )?(admin|system)",
            Pattern.CASE_INSENSITIVE);

    private final JdbcClient jdbc;
    private final EmbeddingService embeddings;
    private final ObjectMapper mapper;
    private final AuditClient audit;

    public IngestionService(JdbcClient jdbc, EmbeddingService embeddings, ObjectMapper mapper, AuditClient audit) {
        this.jdbc = jdbc;
        this.embeddings = embeddings;
        this.mapper = mapper;
        this.audit = audit;
    }

    @Transactional
    public IngestResult ingest(IngestRequest req) {
        String hash = sha256(req.content());
        Optional<Map<String, Object>> existing = jdbc.sql(
                "SELECT id, content_hash FROM policy_document WHERE policy_key = :k AND version = :v")
                .param("k", req.policyKey()).param("v", req.version()).query().listOfRows().stream().findFirst();
        if (existing.isPresent()) {
            if (hash.equals(existing.get().get("content_hash"))) {
                long id = ((Number) existing.get().get("id")).longValue();
                return new IngestResult(id, req.policyKey(), req.version(), 0, false, false);
            }
            throw new ApiException(HttpStatus.CONFLICT, "POLICY_VERSION_EXISTS",
                    "Version " + req.version() + " of " + req.policyKey() + " already exists with different content; publish a new version");
        }
        long docId = jdbc.sql("""
                INSERT INTO policy_document (policy_key, version, title, content, content_hash, effective_from, effective_to, region)
                VALUES (:k, :v, :t, :c, :h, :ef, :et, :r) RETURNING id""")
                .param("k", req.policyKey()).param("v", req.version()).param("t", req.title()).param("c", req.content())
                .param("h", hash).param("ef", req.effectiveFrom()).param("et", req.effectiveTo())
                .param("r", req.region() == null ? "ALL" : req.region()).query(Long.class).single();

        List<String> chunks = Chunker.split(req.content(), 600, 80);
        boolean anyInjection = false;
        for (int i = 0; i < chunks.size(); i++) {
            String text = chunks.get(i);
            boolean suspicious = INJECTION.matcher(text).find();
            anyInjection |= suspicious;
            String meta;
            try {
                meta = mapper.writeValueAsString(Map.of("policyKey", req.policyKey(), "version", req.version(),
                        "policyType", req.policyType() == null ? "general" : req.policyType(),
                        "region", req.region() == null ? "ALL" : req.region(),
                        "embedder", embeddings.provider(), "injectionSuspected", suspicious));
            } catch (JsonProcessingException e) {
                throw new IllegalStateException(e);
            }
            jdbc.sql("""
                    INSERT INTO policy_chunk (policy_document_id, chunk_index, content, metadata, embedding)
                    VALUES (:d, :i, :c, CAST(:m AS jsonb), CAST(:e AS vector))""")
                    .param("d", docId).param("i", i).param("c", text).param("m", meta)
                    .param("e", EmbeddingService.toLiteral(embeddings.embed(text))).update();
        }
        if (anyInjection) {
            audit.record(null, "policy-service", "PROMPT_INJECTION_SUSPECTED_IN_POLICY", null,
                    Map.of("policyKey", req.policyKey(), "version", req.version()));
        }
        return new IngestResult(docId, req.policyKey(), req.version(), chunks.size(), true, anyInjection);
    }

    private static String sha256(String s) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(s.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
