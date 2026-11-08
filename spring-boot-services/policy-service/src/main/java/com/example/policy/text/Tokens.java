package com.example.policy.text;

import java.util.Arrays;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

/** Small text helpers shared by ingestion and retrieval. */
public final class Tokens {

    private static final Pattern WORD = Pattern.compile("[a-z0-9]+");
    private static final Set<String> STOP = Set.of("the", "and", "for", "are", "was", "with", "that", "this", "from", "have",
            "has", "not", "you", "your", "can", "will", "what", "when", "how", "does", "about", "please", "may", "any", "all",
            "its", "who", "which", "into", "than", "then", "they", "their", "been", "being", "should", "would", "could", "also",
            "only", "must", "within", "after", "before", "per", "our", "out", "off", "get", "got", "did", "can't");

    private static final Pattern INJECTION = Pattern.compile(
            "ignore (all )?(the )?(previous|prior|above) instructions|disregard .{0,40}instructions"
                    + "|you (must|should) (now )?(issue|approve|process) (a )?refund|system prompt|act as (an? )?(admin|system)"
                    + "|reveal your (prompt|instructions)", Pattern.CASE_INSENSITIVE);

    private Tokens() {
    }

    /** True if the text looks like an attempt to give instructions to a model (retrieved text is data, never instructions). */
    public static boolean suspicious(String text) {
        return text != null && INJECTION.matcher(text).find();
    }

    /** Rough token estimate (about 1.3 tokens per word), good enough for budgets. */
    public static int estimate(String text) {
        if (text == null || text.isBlank()) {
            return 0;
        }
        return (int) Math.ceil(text.trim().split("\\s+").length * 1.3);
    }

    /** Lower-cased content words (stop words removed). */
    public static List<String> terms(String text) {
        var m = WORD.matcher(text == null ? "" : text.toLowerCase());
        var out = new java.util.ArrayList<String>();
        while (m.find()) {
            String w = m.group();
            if (w.length() >= 3 && !STOP.contains(w)) {
                out.add(w);
            }
        }
        return out;
    }

    /** Very light stemmer so "delayed", "delays" and "delay" match each other. */
    public static String stem(String w) {
        for (String suffix : new String[] {"ing", "ed", "es", "s"}) {
            if (w.length() > suffix.length() + 3 && w.endsWith(suffix)) {
                return w.substring(0, w.length() - suffix.length());
            }
        }
        return w;
    }

    public static Set<String> stems(String text) {
        return terms(text).stream().map(Tokens::stem).collect(Collectors.toCollection(java.util.LinkedHashSet::new));
    }

    public static List<String> bigrams(List<String> terms) {
        return java.util.stream.IntStream.range(0, Math.max(0, terms.size() - 1))
                .mapToObj(i -> terms.get(i) + " " + terms.get(i + 1)).toList();
    }

    public static double jaccard(Set<String> a, Set<String> b) {
        if (a.isEmpty() || b.isEmpty()) {
            return 0;
        }
        long inter = a.stream().filter(b::contains).count();
        return (double) inter / (a.size() + b.size() - inter);
    }

    public static String[] sentences(String text) {
        var it = java.text.BreakIterator.getSentenceInstance(java.util.Locale.ENGLISH);
        it.setText(text);
        var out = new java.util.ArrayList<String>();
        int start = it.first();
        for (int end = it.next(); end != java.text.BreakIterator.DONE; start = end, end = it.next()) {
            String s = text.substring(start, end).trim();
            if (!s.isEmpty()) {
                out.add(s);
            }
        }
        return out.toArray(new String[0]);
    }

    public static String join(String... parts) {
        return Arrays.stream(parts).filter(p -> p != null && !p.isBlank()).collect(Collectors.joining(" "));
    }
}
