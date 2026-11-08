package com.example.policy.text;

import java.util.regex.Pattern;

/** Flags text that looks like an attempt to give instructions to a model. */
public final class InjectionScanner {

    private static final Pattern INJECTION = Pattern.compile(
            "ignore (all )?(the )?(previous|prior|above) instructions|disregard .{0,40}instructions"
                    + "|you (must|should) (now )?(issue|approve|process) (a )?refund|system prompt|act as (an? )?(admin|system)"
                    + "|reveal your (prompt|instructions)", Pattern.CASE_INSENSITIVE);

    private InjectionScanner() {
    }

    public static boolean suspicious(String text) {
        return text != null && INJECTION.matcher(text).find();
    }
}
