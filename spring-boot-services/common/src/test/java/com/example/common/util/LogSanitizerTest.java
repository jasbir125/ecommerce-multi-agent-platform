package com.example.common.util;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class LogSanitizerTest {

    @Test
    void masksSecretsInJsonButKeepsBusinessValues() {
        String out = LogSanitizer.clean("{\"orderId\":\"ORD-1\",\"amount\":129.99,\"password\":\"hunter2\",\"accessToken\":\"abc.def.ghi\"}", 500);
        assertTrue(out.contains("ORD-1") && out.contains("129.99"));
        assertFalse(out.contains("hunter2"));
        assertTrue(out.contains("\"password\":\"***\""));
        assertTrue(out.contains("\"accessToken\":\"***\""));
    }

    @Test
    void masksJwtsBearerKeysEmailsAndPhones() {
        String jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhbGljZSJ9.c2lnbmF0dXJlc2lnbg";
        String out = LogSanitizer.clean("auth Bearer " + jwt + " mail alice@example.com call +1 415 555 2671 key sk-proj-abcdefghijklmnop1234", 500);
        assertFalse(out.contains("eyJ"));
        assertFalse(out.contains("alice@example.com"));
        assertFalse(out.contains("555 2671"));
        assertFalse(out.contains("sk-proj"));
        assertTrue(out.contains("[email]") && out.contains("[phone]"));
    }

    @Test
    void doesNotMistakeDatesOrOrderIdsForPhoneNumbers() {
        assertEquals("ORD-8801 on 2026-11-08T10:00:00Z", LogSanitizer.clean("ORD-8801 on 2026-11-08T10:00:00Z", 100));
    }

    @Test
    void truncatesAndFlattensWhitespace() {
        String out = LogSanitizer.clean("line1\n   line2 " + "x".repeat(100), 20);
        assertTrue(out.startsWith("line1 line2 xxxxxxxx"));
        assertTrue(out.contains("...(+"));
        assertEquals("", LogSanitizer.clean(null, 10));
    }
}
