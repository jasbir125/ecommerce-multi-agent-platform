package com.example.policy.ingest;

import java.text.Normalizer;
import java.util.regex.Pattern;

/** Normalises extracted text so identical content always hashes and embeds the same way. */
public final class TextCleaner {

    private static final Pattern CONTROL = Pattern.compile("[\\p{Cntrl}&&[^\\n\\t]]|[\\u200B-\\u200D\\uFEFF]");
    private static final Pattern HYPHEN_BREAK = Pattern.compile("(\\w)-\\n(\\w)");
    private static final Pattern EMPHASIS = Pattern.compile("(\\*\\*|__|`)");
    private static final Pattern SPACES = Pattern.compile("[ \\t\\u00A0]+");
    private static final Pattern BLANKS = Pattern.compile("\\n{3,}");

    private TextCleaner() {
    }

    public static String clean(String text) {
        if (text == null) {
            return "";
        }
        String t = Normalizer.normalize(text, Normalizer.Form.NFKC);
        t = CONTROL.matcher(t).replaceAll("");
        t = HYPHEN_BREAK.matcher(t).replaceAll("$1$2");
        t = EMPHASIS.matcher(t).replaceAll("");
        t = t.replace('\u201C', '"').replace('\u201D', '"').replace('\u2018', '\'').replace('\u2019', '\'');
        t = t.replaceAll("(?m)^[ \\t]*[-*\\u2022]\\s+", "- ");     // unify bullets
        t = SPACES.matcher(t).replaceAll(" ");
        t = t.replaceAll("(?m)[ \\t]+$", "");
        t = BLANKS.matcher(t).replaceAll("\n\n");
        return t.strip();
    }
}
