package com.example.policy.ingest;

import com.example.policy.ingest.TextExtractor.Block;
import com.example.policy.text.Tokens;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Function;

/**
 * Splits blocks into retrieval chunks.
 * 1. Never crosses a heading. 2. Works on whole sentences. 3. In semantic mode it also starts a new chunk where
 * the meaning shifts (similarity between neighbouring sentences dips below the block's mean minus one standard
 * deviation). 4. Caps chunk size in tokens. 5. Repeats the last sentence of a chunk at the start of the next
 * (overlap) so a fact on the boundary stays retrievable. The heading is kept in every chunk.
 */
public final class SemanticChunker {

    public record Chunk(int index, String section, String content, int tokens) {
    }

    public record Config(int maxTokens, int minTokens, int overlapTokens, boolean semantic) {
        public static Config defaults() {
            return new Config(220, 60, 45, true);
        }
    }

    private final Config cfg;
    private final Function<List<String>, List<float[]>> embedder;

    public SemanticChunker(Config cfg, Function<List<String>, List<float[]>> embedder) {
        this.cfg = cfg;
        this.embedder = embedder;
    }

    public List<Chunk> chunk(List<Block> blocks) {
        List<Chunk> out = new ArrayList<>();
        for (Block b : blocks) {
            String[] sentences = splitSentences(b.body());
            if (sentences.length == 0) {
                continue;
            }
            boolean[] breakAfter = cfg.semantic() && sentences.length > 3 ? semanticBreaks(sentences) : new boolean[sentences.length];
            List<String> cur = new ArrayList<>();
            int curTokens = 0;
            for (int i = 0; i < sentences.length; i++) {
                int t = Tokens.estimate(sentences[i]);
                if (!cur.isEmpty() && curTokens + t > cfg.maxTokens()) {
                    emit(out, b.heading(), cur);
                    cur = overlap(cur);
                    curTokens = cur.stream().mapToInt(Tokens::estimate).sum();
                }
                cur.add(sentences[i]);
                curTokens += t;
                if (breakAfter[i] && curTokens >= cfg.minTokens() && i < sentences.length - 1) {
                    emit(out, b.heading(), cur);
                    cur = overlap(cur);
                    curTokens = cur.stream().mapToInt(Tokens::estimate).sum();
                }
            }
            if (!cur.isEmpty()) {
                mergeOrEmit(out, b.heading(), cur);
            }
        }
        // Re-number after merges.
        List<Chunk> numbered = new ArrayList<>();
        for (int i = 0; i < out.size(); i++) {
            Chunk c = out.get(i);
            numbered.add(new Chunk(i, c.section(), c.content(), c.tokens()));
        }
        return numbered;
    }

    private List<String> overlap(List<String> chunk) {
        List<String> carry = new ArrayList<>();
        int tokens = 0;
        for (int i = chunk.size() - 1; i >= 0 && carry.size() < 2; i--) {
            int t = Tokens.estimate(chunk.get(i));
            if (tokens + t > cfg.overlapTokens()) {
                break;
            }
            carry.add(0, chunk.get(i));
            tokens += t;
        }
        return carry;
    }

    private void emit(List<Chunk> out, String heading, List<String> sentences) {
        out.add(build(heading, sentences));
    }

    /** A short last chunk is folded into the previous chunk of the same section instead of standing alone. */
    private void mergeOrEmit(List<Chunk> out, String heading, List<String> sentences) {
        Chunk c = build(heading, sentences);
        if (c.tokens() < cfg.minTokens() && !out.isEmpty() && java.util.Objects.equals(out.get(out.size() - 1).section(), heading)) {
            Chunk prev = out.remove(out.size() - 1);
            String body = prev.content() + " " + String.join(" ", sentences);
            out.add(new Chunk(0, heading, body, Tokens.estimate(body)));
        } else {
            out.add(c);
        }
    }

    private Chunk build(String heading, List<String> sentences) {
        String body = String.join(" ", sentences);
        String content = heading == null ? body : heading + ": " + body;
        return new Chunk(0, heading, content, Tokens.estimate(content));
    }

    /** Hard-splits any sentence longer than the chunk limit so no single sentence can overflow it. */
    private String[] splitSentences(String body) {
        List<String> out = new ArrayList<>();
        for (String line : body.split("\\n")) {
            String l = line.strip();
            if (l.isEmpty()) {
                continue;
            }
            for (String s : Tokens.sentences(l)) {
                String[] words = s.split("\\s+");
                int limit = (int) (cfg.maxTokens() / 1.3);
                for (int i = 0; i < words.length; i += limit) {
                    out.add(String.join(" ", java.util.Arrays.copyOfRange(words, i, Math.min(words.length, i + limit))));
                }
            }
        }
        return out.toArray(new String[0]);
    }

    private boolean[] semanticBreaks(String[] sentences) {
        List<float[]> v = embedder.apply(List.of(sentences));
        double[] sim = new double[sentences.length - 1];
        double mean = 0;
        for (int i = 0; i < sim.length; i++) {
            sim[i] = cosine(v.get(i), v.get(i + 1));
            mean += sim[i];
        }
        mean /= sim.length;
        double var = 0;
        for (double s : sim) {
            var += (s - mean) * (s - mean);
        }
        double threshold = mean - Math.sqrt(var / sim.length);
        boolean[] breaks = new boolean[sentences.length];
        for (int i = 0; i < sim.length; i++) {
            breaks[i] = sim[i] < threshold;
        }
        return breaks;
    }

    static double cosine(float[] a, float[] b) {
        double dot = 0, na = 0, nb = 0;
        for (int i = 0; i < a.length; i++) {
            dot += a[i] * b[i];
            na += a[i] * a[i];
            nb += b[i] * b[i];
        }
        return na == 0 || nb == 0 ? 0 : dot / (Math.sqrt(na) * Math.sqrt(nb));
    }
}
