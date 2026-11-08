package com.example.policy.ingest;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class TextCleanerTest {

    @Test
    void normalisesQuotesSpacesHyphenationAndEmphasis() {
        String raw = "The \u201Cdeliv-\nery\u201D   was **late**.\u200B\n\n\n\nNext\u00A0paragraph";
        String out = TextCleaner.clean(raw);
        assertThat(out).isEqualTo("The \"delivery\" was late.\n\nNext paragraph");
    }

    @Test
    void identicalContentCleansIdentically() {
        assertThat(TextCleaner.clean("  a  b \n")).isEqualTo(TextCleaner.clean("a b"));
    }

    @Test
    void nullIsEmpty() {
        assertThat(TextCleaner.clean(null)).isEmpty();
    }
}
