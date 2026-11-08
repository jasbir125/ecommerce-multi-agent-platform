package com.example.policy.ingest;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.Test;

class TextExtractorTest {

    @Test
    void markdownHeadingsBecomeSections() {
        List<TextExtractor.Block> b = TextExtractor.extract("# Title\nIntro text.\n\n## Refunds\nPaid back in 5 days.\n## Limits\nMax 500.", "text/markdown");
        assertThat(b).extracting(TextExtractor.Block::heading).containsExactly("Title", "Refunds", "Limits");
        assertThat(b.get(1).body()).contains("Paid back in 5 days.");
    }

    @Test
    void textBeforeTheFirstHeadingIsKept() {
        List<TextExtractor.Block> b = TextExtractor.extract("Plain intro line.\n## A\nBody", "text/markdown");
        assertThat(b.get(0).heading()).isNull();
        assertThat(b.get(0).body()).isEqualTo("Plain intro line.");
    }

    @Test
    void htmlDropsScriptsAndNavigation() {
        String html = "<html><body><nav>menu</nav><script>alert(1)</script><h2>Delays</h2><p>Three days late.</p><li>Or lost.</li><footer>x</footer></body></html>";
        List<TextExtractor.Block> b = TextExtractor.extract(html, "text/html");
        assertThat(b).hasSize(1);
        assertThat(b.get(0).heading()).isEqualTo("Delays");
        assertThat(b.get(0).body()).contains("Three days late.").contains("Or lost.").doesNotContain("alert").doesNotContain("menu");
    }

    @Test
    void normalisesQuotesSpacesHyphenationAndEmphasis() {
        String raw = "The \u201Cdeliv-\nery\u201D   was **late**.\u200B\n\n\n\nNext\u00A0paragraph";
        String out = TextExtractor.clean(raw);
        assertThat(out).isEqualTo("The \"delivery\" was late.\n\nNext paragraph");
    }

    @Test
    void identicalContentCleansIdentically() {
        assertThat(TextExtractor.clean("  a  b \n")).isEqualTo(TextExtractor.clean("a b"));
    }

    @Test
    void nullIsEmpty() {
        assertThat(TextExtractor.clean(null)).isEmpty();
    }
}
