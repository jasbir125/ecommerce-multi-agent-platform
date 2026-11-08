package com.example.policy.embedding;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class EmbeddingServiceTest {

    @Test
    void cacheEncodingRoundTrips() {
        float[] v = {0.5f, -1.25f, 3.14159f};
        assertThat(EmbeddingService.decode(EmbeddingService.encode(v))).containsExactly(v);
    }

    @Test
    void hashEmbeddingIsDeterministicNormalisedAndSharesWordsWithSimilarText() {
        float[] a = EmbeddingService.hashEmbed("delayed shipment refund");
        float[] b = EmbeddingService.hashEmbed("delayed shipment refund");
        float[] c = EmbeddingService.hashEmbed("refund for delayed shipment");
        float[] d = EmbeddingService.hashEmbed("warranty battery capacity");
        assertThat(a).containsExactly(b);
        assertThat(a).hasSize(EmbeddingService.DIMENSIONS);
        assertThat(dot(a, c)).isGreaterThan(0.8f);
        assertThat(dot(a, d)).isLessThan(0.3f);
    }

    @Test
    void literalMatchesPgvectorFormat() {
        assertThat(EmbeddingService.literal(new float[] {1f, 2.5f})).isEqualTo("[1.0,2.5]");
    }

    private static float dot(float[] a, float[] b) {
        float s = 0;
        for (int i = 0; i < a.length; i++) {
            s += a[i] * b[i];
        }
        return s;
    }
}
