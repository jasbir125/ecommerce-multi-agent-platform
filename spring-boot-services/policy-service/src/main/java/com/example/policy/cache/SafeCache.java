package com.example.policy.cache;

import java.time.Duration;
import java.util.Optional;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

/**
 * Redis wrapper where every failure is swallowed: a cache outage makes things slower, never wrong.
 * The "corpus version" counter is part of every query-cache key, so bumping it on ingestion or archiving
 * invalidates all cached answers at once.
 */
@Component
public class SafeCache {

    private static final Logger log = LoggerFactory.getLogger(SafeCache.class);
    static final String CORPUS_KEY = "policy:corpus_version";

    private final StringRedisTemplate redis;

    @org.springframework.beans.factory.annotation.Autowired
    public SafeCache(ObjectProvider<StringRedisTemplate> redis) {
        this.redis = redis.getIfAvailable();
    }

    /** A cache that never stores anything (tests, or running without Redis). */
    public SafeCache() {
        this.redis = null;
    }

    public Optional<String> get(String key) {
        try {
            return redis == null ? Optional.empty() : Optional.ofNullable(redis.opsForValue().get(key));
        } catch (RuntimeException e) {
            log.debug("cache get failed: {}", e.getMessage());
            return Optional.empty();
        }
    }

    public void set(String key, String value, Duration ttl) {
        try {
            if (redis != null) {
                redis.opsForValue().set(key, value, ttl);
            }
        } catch (RuntimeException e) {
            log.debug("cache set failed: {}", e.getMessage());
        }
    }

    public String corpusVersion() {
        return get(CORPUS_KEY).orElse("0");
    }

    public void bumpCorpusVersion() {
        try {
            if (redis != null) {
                redis.opsForValue().increment(CORPUS_KEY);
            }
        } catch (RuntimeException e) {
            log.warn("could not invalidate the policy cache (entries expire on their own): {}", e.getMessage());
        }
    }
}
