# RAG: pipeline and optimisation

The policy RAG is split across two services. **policy-service** (Spring Boot) owns the data: ingestion, embeddings, hybrid
retrieval, query normalisation and synonym expansion, reranking, compression, caching and the final deterministic eligibility check.
**agent-host** (`app/rag`) owns the language-model steps: building the evidence, grounded generation and the grounding validator. Agents reach
retrieval through the policy MCP server, so every call is authenticated, audited and scrubbed.

```
 ingest ─► extract ─► clean ─► hash/dedup ─► semantic chunk ─► reuse│batch-embed ─► store (atomic) ─► bump corpus version
 
 question ─► [agent-host] search (policy-service does the work below) ─► confidence gate
                            ├─► region precedence ─► context (dedupe, injection scan, token budget)
                            └─► generate (LLM, cited statements) ─► validate ─► retry once ─► extractive fallback ─► answer │ "could not verify"

 [policy-service] search: cache? ─► [embed ─► vector (HNSW)] ‖ [keyword + synonyms] ─► RRF ─► rerank ─► diversity ─► confidence gate ─► compress ─► cache
```

## What was built, against the requirements

| Requirement | Where / how |
|---|---|
| Async ingestion pipeline | `POST /api/policies/ingest` returns 202 + job id; bounded worker pool; `ingestion_job` table; interrupted jobs are failed on restart |
| Text extraction, cleaning, normalisation | `TextExtractor` (text, markdown, HTML via jsoup), `TextCleaner` (NFKC, control chars, hyphenation, bullets, quotes) |
| Semantic chunking with overlap | `SemanticChunker`: heading-aware, whole sentences, new chunk where neighbouring-sentence similarity dips, token cap, one-sentence overlap, tiny tails merged |
| Metadata enrichment and filtering | document: type, region, tenant, segment, product category, language, status, effective dates; chunk: section, tokens, keywords, injection flag. Filters applied in SQL |
| Batch embeddings, embedding cache | batches of 64; Redis cache keyed by (model, text hash), 30 days |
| Incremental updates, dedup by hash | document hash (same text = `UNCHANGED`, changed text under a published version = refused); chunk hash reuse across versions (a one-section edit re-embeds one chunk) |
| Version-aware retrieval | latest in-force version per policy key for the requested date; `version` pins one; archived/draft documents never searched |
| Hybrid search + HNSW | pgvector HNSW (`m=16, ef_construction=64`, `ef_search=100`, iterative scan) + Postgres full-text with synonym expansion, fused with reciprocal-rank fusion |
| Reranking | `Reranker` interface; `FeatureReranker` (semantic first, lexical as support) in policy-service, applied before results are returned |
| Query rewriting / expansion | rule-based: normalisation and synonym expansion for keyword search (policy-service `QueryProcessor`) |
| Context compression, token budget | sentence-level compression per chunk; total budget (450 tokens service side, 700 agent side); at most 3 chunks per document; near-duplicates dropped |
| Redis caching | embeddings; retrieval results (key includes filters, corpus version, embedding model, ranking version); the service-side cache is what the agent host reports as a cache hit |
| Cache invalidation | corpus version counter bumped on ingest/archive; `ranking-version` in the key so a deploy never serves old-logic results |
| Connection pooling, read replica | Hikari pool tuned; `POLICY_READ_DATASOURCE_URL` routes retrieval to a replica (primary otherwise); 3 s query timeout |
| Circuit breakers, timeouts, retries | embedding: retry (3, exponential) + circuit breaker; agent-host search: breaker (3 failures → open 30 s) + per-call timeout; models: timeout then fallback; retries only for transient errors |
| Async I/O and parallel retrieval | Python fully async; query variants searched in parallel; vector and keyword branches run in parallel |
| Degraded mode | embedding outage → keyword-only retrieval, flagged `degraded`, confidence × 0.9; nothing available → HTTP 503, never a guess |
| Region precedence | a region-specific policy overrides the global one on the same topic, enforced in code in both services |
| Observability | `rag_query_log` table; Micrometer metrics (`rag.retrieval.latency`, `rag.embedding.cache`, `rag.embedding.provider.latency`, `rag.retrieval.insufficient`, branch failures, ingested documents) |
| Evaluation | golden set (42), hold-out set (13), adversarial set (8), runners with quality gates — see `docs/evaluation.md` |

## Configuration

policy-service (`application.yml`): `policy.embedding-provider` (`openai`/`hash`), `policy.chunking.*`, `policy.retrieval.*`
(`candidates`, `max-chunks-per-document`, `max-context-tokens`, `min-confidence`, `cache-ttl-seconds`, `ranking-version`),
`policy.datasource.read.url`. agent-host (`.env`): `RAG_MAX_CONTEXT_TOKENS`, `RAG_TOP_K`, `RAG_MIN_RETRIEVAL_CONFIDENCE`, `RAG_MIN_SUPPORT`, `RAG_LLM_TIMEOUT`.

## Measured behaviour (OpenAI embeddings and gpt-4o-mini, local stack)

| | |
|---|---|
| Retrieval, uncached (embedding call included) | ~600 ms; cached: 1–3 ms |
| Full answer | p50 ~1.1 s, p95 ~1.7 s, about 450 tokens (about $0.0001); retrieval served from the policy-service cache when repeated |
| Ingest a 4-section document | ~1 s (embedding bound); an edited version re-embeds only changed chunks |

## Tuning notes (what the data said)

- Embedding similarity separated answerable from unanswerable questions far better than word overlap: relevant chunks scored
  0.42–0.77 (median 0.63), out-of-scope questions topped out at 0.42 (median 0.30). The reranker therefore leads with the
  semantic score (`scaleSimilarity` maps 0.30–0.70 to 0–1) and uses lexical features as support.
- The service's abstention threshold (`min-confidence: 0.35`) is deliberately conservative: when in doubt, abstain and escalate.
  Paraphrases that fall just under it are answered with "could not verify" and escalated, not guessed.
- Re-tune `scaleSimilarity` and `min-confidence` if you change the embedding model: vectors from different models are not
  comparable (chunks record their `embedding_model`, and search only compares like with like). Re-ingest after a change.
- Ties among chunks that all mention "refund" and "days" are broken by the feature reranker; the context builder passes the top few
  chunks to the model and the grounding validator rejects any statement the cited chunks do not support.

## Operations

```sql
-- retrieval quality and latency over the last hour
SELECT count(*) AS queries, round(avg(latency_ms)) AS avg_ms,
       round(100.0 * count(*) FILTER (WHERE cache_hit) / count(*)) AS cache_pct,
       round(100.0 * count(*) FILTER (WHERE insufficient) / count(*)) AS abstain_pct,
       count(*) FILTER (WHERE degraded) AS degraded
FROM rag_query_log WHERE created_at > now() - interval '1 hour';
```
Add a new policy version with `POST /api/policies/ingest` (ADMIN role); remove one with `POST /api/policies/{id}/archive`. Both invalidate
cached answers immediately.

## Known limits
- Rerank without a cross-encoder: the feature reranker is a heuristic. There is no model-based relevance check, so an off-topic passage that scores above the confidence gate could be cited; the grounding validator only guarantees the answer is supported by the cited text.
- Embeddings are 1,536-dimensional (`VECTOR(1536)`); a different dimension needs a schema change.
- The in-memory rate limits and circuit breaker are per process.
- Amazon Bedrock Knowledge Base as an alternative retriever is planned for Phase 8; `PolicyRetriever` takes any async search function, so it can be swapped in.
