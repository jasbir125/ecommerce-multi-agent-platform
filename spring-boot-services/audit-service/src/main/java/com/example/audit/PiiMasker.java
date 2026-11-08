package com.example.audit;

import java.util.regex.Pattern;

/** Masks emails, phone numbers and card-like numbers before anything is stored. */
final class PiiMasker {

    private static final Pattern EMAIL = Pattern.compile("([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\\.[A-Za-z]{2,})");
    private static final Pattern CARD = Pattern.compile("\\b(?:\\d[ -]?){13,16}\\b");
    private static final Pattern PHONE = Pattern.compile("(?<!\\d)\\+?\\d[\\d -]{8,}\\d(?!\\d)");

    private PiiMasker() {
    }

    static String mask(String text) {
        if (text == null) {
            return null;
        }
        String out = EMAIL.matcher(text).replaceAll("$1***@$2");
        out = CARD.matcher(out).replaceAll("[CARD-REDACTED]");
        return PHONE.matcher(out).replaceAll("[PHONE-REDACTED]");
    }
}
