package com.example.common.security;

import java.util.Set;

/** Who is calling: a trusted caller (service token) acting for a customer, with the roles it presents in headers. */
public record Caller(String customerId, Set<String> roles, String correlationId) {

    public boolean hasRole(String role) {
        return roles.contains(role.toUpperCase());
    }
}
