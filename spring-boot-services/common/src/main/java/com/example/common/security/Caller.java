package com.example.common.security;

import java.util.Set;

/**
 * Who is calling. Two kinds:
 * - a trusted platform component (service token): customerId/roles come from headers it sets on the user's behalf;
 * - an end user (signed JWT): customerId/roles come from the verified token, never from headers.
 */
public record Caller(String customerId, Set<String> roles, String correlationId, boolean jwt, String subject) {

    public boolean hasRole(String role) {
        return roles.contains(role.toUpperCase());
    }
}
