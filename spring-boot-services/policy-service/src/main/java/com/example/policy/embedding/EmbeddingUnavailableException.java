package com.example.policy.embedding;

/** The embedding provider is down, too slow, or its circuit breaker is open. */
public class EmbeddingUnavailableException extends RuntimeException {
    public EmbeddingUnavailableException(String message, Throwable cause) {
        super(message, cause);
    }
}
