package com.example.policy;

import com.example.common.web.ApiException;
import java.util.regex.Pattern;
import org.springframework.ai.embedding.EmbeddingModel;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

/**
 * Produces 1536-dimension vectors. "openai" uses Spring AI's EmbeddingModel; "hash"
 * is an offline feature-hashing fallback good enough for local demos. Vectors from
 * different providers are not comparable, so the provider is stored with each chunk.
 */
@Service
public class EmbeddingService {

    public static final int DIMENSIONS = 1536;
    private static final Pattern TOKEN = Pattern.compile("[a-z0-9]+");

    private final EmbeddingModel model; // null unless provider=openai
    private final String provider;

    public EmbeddingService(ObjectProvider<EmbeddingModel> model, @Value("${policy.embedding-provider}") String provider) {
        this.model = "openai".equals(provider) ? model.getIfAvailable() : null;
        this.provider = this.model != null ? "openai" : "hash";
    }

    public String provider() {
        return provider;
    }

    public float[] embed(String text) {
        if (model != null) {
            try {
                return model.embed(text);
            } catch (RuntimeException e) {
                throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "EMBEDDING_UNAVAILABLE", "Embedding provider failed: " + e.getMessage());
            }
        }
        return hashEmbed(text);
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
    public static String toLiteral(float[] v) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < v.length; i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append(v[i]);
        }
        return sb.append(']').toString();
    }
}
