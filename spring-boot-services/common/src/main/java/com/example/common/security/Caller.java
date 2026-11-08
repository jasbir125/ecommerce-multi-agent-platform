package com.example.common.security;

import java.util.Set;

/**
 * Identity of whoever is calling this service, taken from request headers.
 * The service token proves the caller is a trusted platform component; the
 * customer id says on whose behalf it is acting (used for ownership checks).
 */
public record Caller(String customerId, Set<String> roles, String correlationId) {

    public boolean hasRole(String role) {
        return roles.contains(role.toUpperCase());
    }
}
