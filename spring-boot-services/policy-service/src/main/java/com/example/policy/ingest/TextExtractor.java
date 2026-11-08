package com.example.policy.ingest;

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

    private TextExtractor() {
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
