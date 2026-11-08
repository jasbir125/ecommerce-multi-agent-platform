package com.example.policy.retrieval;

import com.example.policy.config.PolicyConfig.ReadJdbc;
import com.example.policy.embedding.EmbeddingService;
import java.util.List;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/** The two candidate generators. Both only look at ACTIVE documents in force for the requested filters. */
@Component
public class CandidateSearcher implements CandidateSource {

    /** Latest in-force version of each policy key that passes the metadata filters. */
    static final String ACTIVE = """
            WITH active AS (
              SELECT DISTINCT ON (policy_key) id, policy_key, version, title, policy_type, effective_from, region, product_category
              FROM policy_document
              WHERE status = 'ACTIVE'
                AND (effective_from IS NULL OR effective_from <= :asof)
                AND (effective_to IS NULL OR effective_to >= :asof)
                AND (region = 'ALL' OR region = :region)
                AND tenant_id IN ('default', :tenant)
                AND (CAST(:segment AS text) IS NULL OR customer_segment IN ('ALL', CAST(:segment AS text)))
                AND (CAST(:category AS text) IS NULL OR product_category IN ('ALL', CAST(:category AS text)))
                AND (CAST(:ptype AS text) IS NULL OR policy_type = CAST(:ptype AS text))
                AND (CAST(:version AS text) IS NULL OR version = CAST(:version AS text))
              ORDER BY policy_key, effective_from DESC NULLS LAST, id DESC)
            """;
    private static final String COLS = """
            SELECT c.id, d.id AS doc_id, d.policy_key, d.version, d.title, d.policy_type, d.effective_from, d.region,
                   d.product_category, c.section,
                   c.content, COALESCE((c.metadata->>'injectionSuspected')::boolean, false) AS injection""";

    private static final RowMapper<Candidate> MAP = (rs, i) -> new Candidate(rs.getLong("id"), rs.getLong("doc_id"),
            rs.getString("policy_key"), rs.getString("version"), rs.getString("title"), rs.getString("policy_type"),
            rs.getObject("effective_from", java.time.LocalDate.class), rs.getString("region"), rs.getString("product_category"),
            rs.getString("section"), rs.getString("content"),
            rs.getBoolean("injection"), rs.getDouble("sim"));

    private final JdbcClient jdbc;

    public CandidateSearcher(ReadJdbc read) {
        this.jdbc = read.client();
    }

    @Override
    public List<Candidate> vector(float[] query, String model, Filters f, int limit) {
        return bind(jdbc.sql(ACTIVE + COLS + """
                , 1 - (c.embedding <=> CAST(:q AS vector)) AS sim
                FROM policy_chunk c JOIN active d ON d.id = c.policy_document_id
                WHERE c.embedding_model = :model
                ORDER BY c.embedding <=> CAST(:q AS vector) LIMIT :lim"""), f)
                .param("q", EmbeddingService.literal(query)).param("model", model).param("lim", limit)
                .query(MAP).list();
    }

    @Override
    public List<Candidate> keyword(String tsQuery, Filters f, int limit) {
        if (tsQuery.isBlank()) {
            return List.of();
        }
        return bind(jdbc.sql(ACTIVE + COLS + """
                , 0.0 AS sim
                FROM policy_chunk c JOIN active d ON d.id = c.policy_document_id,
                     to_tsquery('english', :tsq) q
                WHERE c.content_tsv @@ q
                ORDER BY ts_rank_cd(c.content_tsv, q) DESC LIMIT :lim"""), f)
                .param("tsq", tsQuery).param("lim", limit).query(MAP).list();
    }

    private JdbcClient.StatementSpec bind(JdbcClient.StatementSpec spec, Filters f) {
        return spec.param("asof", f.asOf()).param("region", f.region()).param("tenant", f.tenant())
                .param("segment", f.segment()).param("category", f.category()).param("ptype", f.policyType())
                .param("version", f.version());
    }
}
