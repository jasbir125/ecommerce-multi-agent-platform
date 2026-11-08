# Bedrock Knowledge Base (alternative RAG)

The default RAG is the platform's own pipeline (pgvector + full-text hybrid retrieval, LLM rerank, grounding validator, versioned policies with region precedence, answer cache). It is what the evaluation datasets measure.

A Bedrock Knowledge Base can replace the retrieval part:

| Need | Configuration |
|---|---|
| Sources | S3 bucket with the policy documents (refund, return, shipping, manuals, support procedures); one prefix per policy key, version in the file name |
| Chunking | Semantic chunking |
| Embeddings | Titan Text Embeddings v2 or Cohere Embed (vector size differs from the platform's 1536: it cannot share the platform's table) |
| Vector store | Aurora PostgreSQL with pgvector or OpenSearch Serverless |
| Retrieval | Hybrid search, metadata filtering (`region`, `policyType`, `version`, `effective_to`), reranking model enabled |
| Attribution | Return the source URI and chunk with every answer (`RetrieveAndGenerate` citations) |
| Freshness | Sync on every S3 change (EventBridge) plus a nightly scheduled sync |

What you give up by switching: the platform's grounding validator, extractive fallback, region-precedence logic, corpus-versioned cache and the RAG evaluation harness would have to be rebuilt around the Knowledge Base responses. Treat it as a migration project, not a config flag. Terraform for it is intentionally not included.
