package com.example.policy.ingest;

import static org.assertj.core.api.Assertions.assertThat;

import com.example.policy.text.Tokens;
import java.util.List;
import org.junit.jupiter.api.Test;

class SemanticChunkerTest {

    /** Sentences about shipping point one way, sentences about money the other: a clear topic shift. */
    private static float[] topicEmbed(String s) {
        float[] v = new float[2];
        boolean money = s.toLowerCase().contains("refund") || s.toLowerCase().contains("money");
        v[money ? 1 : 0] = 1;
        return v;
    }

    private SemanticChunker chunker(int max, int min, int overlap, boolean semantic) {
        return new SemanticChunker(new SemanticChunker.Config(max, min, overlap, semantic),
                texts -> texts.stream().map(SemanticChunkerTest::topicEmbed).toList());
    }

    private static String sentences(String prefix, int n) {
        StringBuilder sb = new StringBuilder();
        for (int i = 1; i <= n; i++) {
            sb.append(prefix).append(" sentence number ").append(i).append(" has several plain words in it. ");
        }
        return sb.toString().strip();
    }

    @Test
    void neverCrossesHeadingsAndKeepsHeadingInEveryChunk() {
        var blocks = List.of(new TextExtractor.Block("Delays", "Shipments can be late. Late means three days."),
                new TextExtractor.Block("Refunds", "Refunds go back to the card."));
        var chunks = chunker(220, 10, 20, false).chunk(blocks);
        assertThat(chunks).hasSize(2);
        assertThat(chunks.get(0).content()).startsWith("Delays: ");
        assertThat(chunks.get(1).content()).startsWith("Refunds: ").doesNotContain("late");
    }

    @Test
    void respectsMaxTokensAndRepeatsASentenceAsOverlap() {
        var chunks = chunker(60, 10, 25, false).chunk(List.of(new TextExtractor.Block("H", sentences("Shipping", 12))));
        assertThat(chunks.size()).isGreaterThan(2);
        assertThat(chunks).allSatisfy(c -> assertThat(c.tokens()).isLessThanOrEqualTo(60 + 30));
        // overlap: the last sentence of chunk 0 also starts chunk 1
        String lastOfFirst = Tokens.sentences(chunks.get(0).content().replaceFirst("^H: ", "")) [Tokens.sentences(chunks.get(0).content().replaceFirst("^H: ", "")).length - 1];
        assertThat(chunks.get(1).content()).contains(lastOfFirst);
    }

    @Test
    void neverSplitsInsideASentence() {
        var chunks = chunker(60, 10, 25, false).chunk(List.of(new TextExtractor.Block(null, sentences("Shipping", 10))));
        assertThat(chunks).allSatisfy(c -> assertThat(c.content()).endsWith("."));
    }

    @Test
    void semanticModeStartsANewChunkWhereTheTopicChanges() {
        String body = "Shipping takes time. Shipping can be late. Shipping uses carriers. Shipping is tracked. "
                + "Refund money goes back. Refund is full. Refund needs approval. Refund is fast.";
        var chunks = chunker(220, 8, 0, true).chunk(List.of(new TextExtractor.Block("Topics", body)));
        assertThat(chunks).hasSize(2);
        assertThat(chunks.get(0).content()).contains("Shipping").doesNotContain("Refund money");
        assertThat(chunks.get(1).content()).startsWith("Topics: Refund");
    }

    @Test
    void aTinyLastChunkIsMergedIntoThePreviousOne() {
        var chunks = chunker(40, 30, 0, false).chunk(List.of(new TextExtractor.Block("H", sentences("Alpha", 6) + " Short end.")));
        assertThat(chunks.get(chunks.size() - 1).tokens()).isGreaterThanOrEqualTo(30);
    }

    @Test
    void aSingleHugeSentenceIsHardSplit() {
        String huge = "word ".repeat(400).strip() + ".";
        var chunks = chunker(60, 10, 0, false).chunk(List.of(new TextExtractor.Block(null, huge)));
        assertThat(chunks).hasSizeGreaterThan(3);
    }
}
