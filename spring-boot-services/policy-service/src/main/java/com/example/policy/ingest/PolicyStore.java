package com.example.policy.ingest;

import com.example.policy.PolicyRecords.*;
import java.sql.Types;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

/** Database access for ingestion. A document becomes visible (ACTIVE) only after all its chunks are stored. */
@Repository
public class PolicyStore {

    public record ChunkRow(int index, String section, String content, String hash, int tokens, String metadataJson,
                           String embeddingLiteral) {
    }

    public record ExistingDoc(long id, String contentHash, String status) {
    }

    private final JdbcClient jdbc;
    private final JdbcTemplate template;

    public PolicyStore(JdbcClient jdbc, JdbcTemplate template) {
        this.jdbc = jdbc;
        this.template = template;
    }

    public Optional<ExistingDoc> find(String key, String version) {
        return jdbc.sql("SELECT id, content_hash, status FROM policy_document WHERE policy_key = :k AND version = :v")
                .param("k", key).param("v", version)
                .query((rs, i) -> new ExistingDoc(rs.getLong("id"), rs.getString("content_hash"), rs.getString("status"))).optional();
    }

    /** Embeddings already stored for identical chunk text from the same model (incremental re-ingestion). */
    public Map<String, String> reusableEmbeddings(Set<String> hashes, String model) {
        if (hashes.isEmpty()) {
            return Map.of();
        }
        Map<String, String> out = new HashMap<>();
        jdbc.sql("""
                SELECT DISTINCT ON (content_hash) content_hash, embedding::text AS emb FROM policy_chunk
                WHERE content_hash IN (:h) AND embedding_model = :m AND embedding IS NOT NULL""")
                .param("h", hashes).param("m", model)
                .query((rs, i) -> out.put(rs.getString("content_hash"), rs.getString("emb"))).list();
        return out;
    }

    @Transactional
    public long save(IngestRequest req, String docHash, List<ChunkRow> chunks, String embeddingModel) {
        find(req.policyKey(), req.version()).filter(d -> !"ACTIVE".equals(d.status()))
                .ifPresent(d -> deleteDocument(d.id()));      // clean up a half-finished earlier attempt
        long id = jdbc.sql("""
                INSERT INTO policy_document (policy_key, version, title, content, content_hash, policy_type, tenant_id,
                    customer_segment, product_category, source_uri, status, effective_from, effective_to, region)
                VALUES (:k, :v, :t, :c, :h, :pt, :tn, :cs, :pc, :su, 'DRAFT', :ef, :et, :r) RETURNING id""")
                .param("k", req.policyKey()).param("v", req.version()).param("t", req.title()).param("c", req.content())
                .param("h", docHash).param("pt", or(req.policyType(), "general")).param("tn", or(req.tenantId(), "default"))
                .param("cs", or(req.customerSegment(), "ALL")).param("pc", or(req.productCategory(), "ALL"))
                .param("su", req.sourceUri()).param("ef", req.effectiveFrom()).param("et", req.effectiveTo())
                .param("r", or(req.region(), "ALL")).query(Long.class).single();
        template.batchUpdate("""
                INSERT INTO policy_chunk (policy_document_id, chunk_index, section, content, content_hash, token_count,
                    metadata, embedding_model, embedding)
                VALUES (?, ?, ?, ?, ?, ?, CAST(? AS jsonb), ?, CAST(? AS vector))""", chunks, 100, (ps, c) -> {
            ps.setLong(1, id);
            ps.setInt(2, c.index());
            ps.setString(3, c.section());
            ps.setString(4, c.content());
            ps.setString(5, c.hash());
            ps.setInt(6, c.tokens());
            ps.setString(7, c.metadataJson());
            ps.setString(8, embeddingModel);
            ps.setObject(9, c.embeddingLiteral(), Types.OTHER);
        });
        jdbc.sql("UPDATE policy_document SET status = 'ACTIVE' WHERE id = :id").param("id", id).update();
        return id;
    }

    private void deleteDocument(long id) {
        jdbc.sql("DELETE FROM policy_chunk WHERE policy_document_id = :id").param("id", id).update();
        jdbc.sql("DELETE FROM policy_document WHERE id = :id").param("id", id).update();
    }

    @Transactional
    public boolean archive(long id) {
        return jdbc.sql("UPDATE policy_document SET status = 'ARCHIVED' WHERE id = :id AND status <> 'ARCHIVED'")
                .param("id", id).update() == 1;
    }

    public List<PolicySummary> list() {
        return jdbc.sql("""
                SELECT id, policy_key, version, title, policy_type, status, region, effective_from, effective_to
                FROM policy_document ORDER BY policy_key, effective_from DESC NULLS LAST, id DESC""")
                .query(PolicySummary.class).list();
    }

    private static String or(String v, String d) {
        return v == null || v.isBlank() ? d : v;
    }
}
