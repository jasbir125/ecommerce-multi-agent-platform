package com.example.policy.embedding;

/** What retrieval needs from an embedding provider (lets tests use a plain fake). */
public interface Embedder {

    float[] embed(String text);

    /** Identifies the vector space; chunks are only compared with query vectors from the same model. */
    String modelId();
}
