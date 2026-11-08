package com.example.policy.ingest;

import java.text.Normalizer;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Pattern;
import org.jsoup.Jsoup;
import org.jsoup.nodes.Document;
import org.jsoup.nodes.Element;

/** Turns raw content (plain text, markdown or HTML) into headed blocks of text. */
public final class TextExtractor {

    /** A run of text under one heading (heading may be null for text before the first heading). */
    public record Block(String heading, String body) {
    }

    private static final Pattern MD_HEADING = Pattern.compile("^#{1,6}\\s+(.+)$");
    private static final Pattern NUMBERED = Pattern.compile("^\\d+(\\.\\d+)*[.)]?\\s+[A-Z][^.!?]{2,80}$");

    private static final Pattern CONTROL = Pattern.compile("[\\p{Cntrl}&&[^\\n\\t]]|[\\u200B-\\u200D\\uFEFF]");
    private static final Pattern HYPHEN_BREAK = Pattern.compile("(\\w)-\\n(\\w)");
    private static final Pattern EMPHASIS = Pattern.compile("(\\*\\*|__|`)");
    private static final Pattern SPACES = Pattern.compile("[ \\t\\u00A0]+");
    private static final Pattern BLANKS = Pattern.compile("\\n{3,}");

    private TextExtractor() {
    }

    /** Normalises extracted text so identical content always hashes and embeds the same way. */
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

    public static List<Block> extract(String content, String contentType) {
        String type = contentType == null ? "text/plain" : contentType.toLowerCase();
        return type.contains("html") ? fromHtml(content) : fromText(content);
    }

    static List<Block> fromText(String text) {
        List<Block> blocks = new ArrayList<>();
        String heading = null;
        StringBuilder body = new StringBuilder();
        for (String raw : text.split("\\R")) {
            String line = raw.strip();
            var md = MD_HEADING.matcher(line);
            boolean isHeading = md.matches() || NUMBERED.matcher(line).matches();
            if (isHeading) {
                flush(blocks, heading, body);
                heading = md.matches() ? md.group(1).strip() : line;
            } else {
                body.append(line).append('\n');
            }
        }
        flush(blocks, heading, body);
        return blocks;
    }

    static List<Block> fromHtml(String html) {
        Document doc = Jsoup.parse(html);
        doc.select("script, style, nav, footer, header, form, noscript").remove();
        List<Block> blocks = new ArrayList<>();
        String heading = null;
        StringBuilder body = new StringBuilder();
        for (Element el : doc.body().select("h1, h2, h3, h4, h5, h6, p, li, td")) {
            if (el.tagName().startsWith("h")) {
                flush(blocks, heading, body);
                heading = el.text().strip();
            } else if (!el.text().isBlank()) {
                body.append(el.text().strip()).append('\n');
            }
        }
        flush(blocks, heading, body);
        return blocks;
    }

    private static void flush(List<Block> blocks, String heading, StringBuilder body) {
        if (body.toString().isBlank() && heading == null) {
            return;
        }
        if (!body.toString().isBlank()) {
            blocks.add(new Block(heading, body.toString().strip()));
        }
        body.setLength(0);
    }
}
