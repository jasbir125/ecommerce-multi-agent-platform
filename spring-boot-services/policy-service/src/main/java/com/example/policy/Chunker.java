package com.example.policy;

import java.util.ArrayList;
import java.util.List;

/** Paragraph-aware chunker: packs paragraphs up to maxChars and repeats a tail as overlap. */
final class Chunker {

    private Chunker() {
    }

    static List<String> split(String text, int maxChars, int overlap) {
        List<String> chunks = new ArrayList<>();
        StringBuilder cur = new StringBuilder();
        for (String para : text.split("\\n\\s*\\n")) {
            String p = para.trim();
            if (p.isEmpty()) {
                continue;
            }
            if (cur.length() + p.length() + 1 > maxChars && cur.length() > 0) {
                chunks.add(cur.toString());
                String tail = cur.substring(Math.max(0, cur.length() - overlap));
                cur = new StringBuilder(tail).append(' ');
            }
            cur.append(p).append(' ');
        }
        if (cur.toString().trim().length() > 0) {
            chunks.add(cur.toString().trim());
        }
        return chunks;
    }
}
