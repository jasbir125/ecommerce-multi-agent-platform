package com.example.policy.retrieval;

import com.example.policy.text.Tokens;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Set;
import org.springframework.stereotype.Component;

/**
 * Keeps only the sentences of a chunk that matter for the query, within a token budget, so the model is
 * never sent whole documents. Original sentence order is preserved.
 */
@Component
public class ContextCompressor {

    public String compress(String content, Set<String> queryStems, int maxTokens) {
        if (Tokens.estimate(content) <= maxTokens) {
            return content;
        }
        String[] sentences = Tokens.sentences(content);
        record Ranked(int index, double score, int tokens) {
        }
        List<Ranked> ranked = new ArrayList<>();
        for (int i = 0; i < sentences.length; i++) {
            Set<String> s = Tokens.stems(sentences[i]);
            double hit = queryStems.isEmpty() ? 0 : (double) s.stream().filter(queryStems::contains).count() / queryStems.size();
            ranked.add(new Ranked(i, hit + (i == 0 ? 0.05 : 0), Tokens.estimate(sentences[i])));
        }
        ranked.sort(Comparator.comparingDouble(Ranked::score).reversed());
        List<Integer> keep = new ArrayList<>();
        int used = 0;
        for (Ranked r : ranked) {
            if (keep.isEmpty() || used + r.tokens() <= maxTokens) {   // always keep the best sentence
                keep.add(r.index());
                used += r.tokens();
            }
        }
        keep.sort(Integer::compare);
        StringBuilder sb = new StringBuilder();
        for (int idx : keep) {
            sb.append(sentences[idx]).append(' ');
        }
        return sb.toString().strip();
    }
}
