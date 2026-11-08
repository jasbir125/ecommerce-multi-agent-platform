package com.example.common.util;

import java.util.regex.Pattern;

/**
 * Makes request/response/event bodies safe to put in a log line: secrets and personal data are masked and the text is
 * cut to a maximum length. Debugging needs the shape and the business values (order ids, amounts, statuses), never
 * passwords, tokens, e-mail addresses or phone numbers.
 */
public final class LogSanitizer {

    private static final Pattern SENSITIVE_JSON_VALUE = Pattern.compile(
            "(\"(?:password|passwd|secret|token|accessToken|refreshToken|idToken|authorization|apiKey|api_key|privateKey|private_key"
                    + "|serviceToken|cardNumber|cvv|ssn)\"\\s*:\\s*)\"(?:[^\"\\\\]|\\\\.)*\"", Pattern.CASE_INSENSITIVE);
    private static final Pattern JWT = Pattern.compile("eyJ[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}");
    private static final Pattern BEARER = Pattern.compile("(?i)bearer\\s+[A-Za-z0-9._~+/=-]{8,}");
    private static final Pattern EMAIL = Pattern.compile("[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}");
    private static final Pattern PHONE = Pattern.compile("(?<![\\w.-])\\+?\\d{1,3}[\\s.-]?\\(?\\d{3}\\)?[\\s.-]?\\d{3}[\\s.-]?\\d{4}(?![\\w-])");
    private static final Pattern OPENAI_KEY = Pattern.compile("sk-[A-Za-z0-9_-]{16,}");

    private LogSanitizer() {
    }

    /** Masks sensitive values, collapses whitespace and truncates to {@code maxChars}. Null-safe. */
    public static String clean(String text, int maxChars) {
        if (text == null || text.isBlank()) {
            return "";
        }
        String s = SENSITIVE_JSON_VALUE.matcher(text).replaceAll("$1\"***\"");
        s = JWT.matcher(s).replaceAll("[jwt]");
        s = BEARER.matcher(s).replaceAll("Bearer ***");
        s = OPENAI_KEY.matcher(s).replaceAll("[api-key]");
        s = EMAIL.matcher(s).replaceAll("[email]");
        s = PHONE.matcher(s).replaceAll("[phone]");
        s = s.replaceAll("\\s*[\\r\\n]+\\s*", " ");
        if (s.length() > maxChars) {
            return s.substring(0, maxChars) + "...(+" + (s.length() - maxChars) + " chars)";
        }
        return s;
    }
}
