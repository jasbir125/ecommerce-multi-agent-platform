package com.example.common.security;

import com.example.common.web.ApiException;
import org.springframework.http.HttpStatus;

/** Thread-local access to the current request's {@link Caller}. */
public final class CallerHolder {

    private static final ThreadLocal<Caller> CURRENT = new ThreadLocal<>();

    private CallerHolder() {
    }

    static void set(Caller caller) {
        CURRENT.set(caller);
    }

    static void clear() {
        CURRENT.remove();
    }

    public static Caller current() {
        Caller c = CURRENT.get();
        return c != null ? c : new Caller(null, java.util.Set.of(), null, false, null);
    }

    /** The customer this request acts for; fails with 401 if none was supplied. */
    public static String requireCustomerId() {
        String id = current().customerId();
        if (id == null || id.isBlank()) {
            throw new ApiException(HttpStatus.UNAUTHORIZED, "CUSTOMER_REQUIRED", "X-Customer-Id header is required");
        }
        return id;
    }

    /** Endpoints that only trusted platform components may call (an end user's token is refused). */
    public static void requireInternal() {
        if (current().jwt()) {
            throw new ApiException(HttpStatus.FORBIDDEN, "INTERNAL_ONLY", "This endpoint is not available to end users");
        }
    }

    /** End users need one of these roles; trusted platform components pass. */
    public static void requireStaffIfUser(String... anyOf) {
        if (current().jwt()) {
            requireRole(anyOf);
        }
    }

    public static void requireRole(String... anyOf) {
        for (String r : anyOf) {
            if (current().hasRole(r)) {
                return;
            }
        }
        throw new ApiException(HttpStatus.FORBIDDEN, "ROLE_REQUIRED", "One of these roles is required: " + String.join(", ", anyOf));
    }
}
