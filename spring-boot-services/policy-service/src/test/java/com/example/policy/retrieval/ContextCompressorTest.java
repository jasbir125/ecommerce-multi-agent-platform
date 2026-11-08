package com.example.policy.retrieval;

import static org.assertj.core.api.Assertions.assertThat;

import com.example.policy.text.Tokens;
import java.util.Set;
import org.junit.jupiter.api.Test;

class ContextCompressorTest {

    private final ContextCompressor c = new ContextCompressor();
    private static final String TEXT = "Intro about the company history and nothing else matters here at all. "
            + "Orders delayed by three days get a full refund. "
            + "The cafeteria opens at nine every morning for staff. "
            + "Refunds go to the original payment method only.";

    @Test
    void shortTextIsReturnedUntouched() {
        assertThat(c.compress("Short text.", Set.of("refund"), 100)).isEqualTo("Short text.");
    }

    @Test
    void keepsTheSentencesThatMatchTheQueryWithinBudget() {
        String out = c.compress(TEXT, Tokens.stems("refund for delayed orders"), 30);
        assertThat(out).contains("Orders delayed by three days get a full refund.").doesNotContain("cafeteria");
        assertThat(Tokens.estimate(out)).isLessThanOrEqualTo(40);
    }

    @Test
    void keepsOriginalSentenceOrder() {
        String out = c.compress(TEXT, Tokens.stems("refund delayed payment method"), 40);
        assertThat(out.indexOf("delayed")).isLessThan(out.indexOf("payment method"));
    }

    @Test
    void alwaysKeepsAtLeastOneSentence() {
        assertThat(c.compress(TEXT, Set.of("zzz"), 1)).isNotBlank();
    }
}
