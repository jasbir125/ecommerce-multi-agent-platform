package com.example.policy.embedding;

import com.example.policy.cache.SafeCache;
import io.github.resilience4j.circuitbreaker.CallNotPermittedException;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerConfig;
import io.github.resilience4j.retry.Retry;
import io.github.resilience4j.retry.RetryConfig;
import io.micrometer.core.instrument.MeterRegistry;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HexFormat;
import java.util.List;
import java.util.Optional;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.ai.embedding.EmbeddingModel;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

/**
 * Produces 1536-dimension vectors.
 * - "openai" uses Spring AI's EmbeddingModel, in batches, behind a retry + circuit breaker; if it keeps
 *   failing callers get {@link EmbeddingUnavailableException} and retrieval degrades to keyword search.
 * - "hash" is an offline feature-hashing fallback for local demos.
 * Every vector is cached in Redis by (model, text hash), so unchanged text is never embedded twice.
 */
@Service
public class EmbeddingService implements Embedder {

    public static final int DIMENSIONS = 1536;
    private static final Logger log = LoggerFactory.getLogger(EmbeddingService.class);
    private static final Pattern TOKEN = Pattern.compile("[a-z0-9]+");

    private final EmbeddingModel model;      // null unless provider=openai
    private final String modelId;
    private final SafeCache cache;
    private final CircuitBreaker breaker;
    private final Retry retry;
    private final MeterRegistry metrics;
    private final int batchSize;
    private final Duration cacheTtl;

    public EmbeddingService(ObjectProvider<EmbeddingModel> provider, SafeCache cache, MeterRegistry metrics,
                            @Value("${policy.embedding-provider}") String configured,
                            @Value("${OPENAI_EMBEDDING_MODEL:text-embedding-3-small}") String openAiModel,
                            @Value("${policy.embedding.batch-size:64}") int batchSize,
                            @Value("${policy.embedding.cache-ttl-days:30}") int cacheTtlDays) {
        this.model = "openai".equals(configured) ? provider.getIfAvailable() : null;
        this.modelId = this.model != null ? "openai:" + openAiModel : "hash-v1";
        this.cache = cache;
        this.metrics = metrics;
        this.batchSize = batchSize;
        this.cacheTtl = Duration.ofDays(cacheTtlDays);
        this.breaker = CircuitBreaker.of("embedding", CircuitBreakerConfig.custom()
                .slidingWindowSize(10).minimumNumberOfCalls(4).failureRateThreshold(50)
                .waitDurationInOpenState(Duration.ofSeconds(20)).build());
        this.retry = Retry.of("embedding", RetryConfig.custom().maxAttempts(3)
                .intervalFunction(io.github.resilience4j.core.IntervalFunction.ofExponentialBackoff(300, 2.0))
                .ignoreExceptions(CallNotPermittedException.class).build());
    }

    /** Identifies the vector space; chunks are only compared with query vectors from the same model. */
    @Override
    public String modelId() {
        return modelId;
    }

    @Override
    public float[] embed(String text) {
        return embedAll(List.of(text)).get(0);
    }

    public List<float[]> embedAll(List<String> texts) {
        float[][] result = new float[texts.size()][];
        List<Integer> missing = new ArrayList<>();
        for (int i = 0; i < texts.size(); i++) {
            Optional<String> hit = cache.get(cacheKey(texts.get(i)));
            if (hit.isPresent()) {
                result[i] = decode(hit.get());
                metrics.counter("rag.embedding.cache", "result", "hit").increment();
            } else {
                missing.add(i);
                metrics.counter("rag.embedding.cache", "result", "miss").increment();
            }
        }
        for (int from = 0; from < missing.size(); from += batchSize) {
            List<Integer> idx = missing.subList(from, Math.min(missing.size(), from + batchSize));
            List<String> batch = idx.stream().map(texts::get).toList();
            List<float[]> vectors = callProvider(batch);
            for (int j = 0; j < idx.size(); j++) {
                result[idx.get(j)] = vectors.get(j);
                cache.set(cacheKey(batch.get(j)), encode(vectors.get(j)), cacheTtl);
            }
        }
        return List.of(result);
    }

    private List<float[]> callProvider(List<String> batch) {
        if (model == null) {
            return batch.stream().map(EmbeddingService::hashEmbed).toList();
        }
        try {
            return Retry.decorateSupplier(retry, CircuitBreaker.decorateSupplier(breaker, () -> {
                long t0 = System.nanoTime();
                List<float[]> out = model.embed(batch);
                metrics.timer("rag.embedding.provider.latency").record(Duration.ofNanos(System.nanoTime() - t0));
                return out;
            })).get();
        } catch (CallNotPermittedException e) {
            throw new EmbeddingUnavailableException("embedding circuit breaker is open", e);
        } catch (RuntimeException e) {
            log.warn("embedding provider failed: {}", e.getMessage());
            throw new EmbeddingUnavailableException("embedding provider failed: " + e.getMessage(), e);
        }
    }

    private String cacheKey(String text) {
        return "emb:" + modelId + ":" + sha256(text);
    }

    static String encode(float[] v) {
        ByteBuffer buf = ByteBuffer.allocate(v.length * 4);
        for (float f : v) {
            buf.putFloat(f);
        }
        return Base64.getEncoder().encodeToString(buf.array());
    }

    static float[] decode(String s) {
        ByteBuffer buf = ByteBuffer.wrap(Base64.getDecoder().decode(s));
        float[] v = new float[buf.remaining() / 4];
        for (int i = 0; i < v.length; i++) {
            v[i] = buf.getFloat();
        }
        return v;
    }

    static float[] hashEmbed(String text) {
        float[] v = new float[DIMENSIONS];
        var m = TOKEN.matcher(text.toLowerCase());
        while (m.find()) {
            int h = m.group().hashCode();
            v[Math.floorMod(h, DIMENSIONS)] += (h & 1) == 0 ? 1f : -1f;
        }
        double norm = 0;
        for (float f : v) {
            norm += f * f;
        }
        norm = Math.sqrt(norm);
        if (norm > 0) {
            for (int i = 0; i < v.length; i++) {
                v[i] /= (float) norm;
            }
        }
        return v;
    }

    /** pgvector text literal, e.g. "[0.1,0.2,...]". */
    public static String literal(float[] v) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < v.length; i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append(v[i]);
        }
        return sb.append(']').toString();
    }

    public static String sha256(String s) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(s.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
