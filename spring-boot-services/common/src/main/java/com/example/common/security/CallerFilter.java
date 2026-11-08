package com.example.common.security;

import com.example.common.PlatformProperties;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.security.MessageDigest;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Authenticates service-to-service calls (shared token) and captures the caller
 * identity + correlation id. Actuator endpoints stay open for health checks/scraping.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 10)
public class CallerFilter extends OncePerRequestFilter {

    public static final String H_TOKEN = "X-Service-Token";
    public static final String H_CUSTOMER = "X-Customer-Id";
    public static final String H_ROLES = "X-Roles";
    public static final String H_CORRELATION = "X-Correlation-Id";

    private final PlatformProperties props;

    public CallerFilter(PlatformProperties props) {
        this.props = props;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return request.getRequestURI().startsWith("/actuator");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain)
            throws ServletException, IOException {
        if (props.securityEnabled() && !tokenMatches(req.getHeader(H_TOKEN))) {
            res.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
            res.setContentType("application/problem+json");
            res.getWriter().write("{\"title\":\"Unauthorized\",\"status\":401,\"code\":\"INVALID_SERVICE_TOKEN\"}");
            return;
        }
        String correlationId = req.getHeader(H_CORRELATION);
        if (correlationId == null || correlationId.isBlank()) {
            correlationId = UUID.randomUUID().toString();
        }
        Set<String> roles = req.getHeader(H_ROLES) == null ? Set.of()
                : Arrays.stream(req.getHeader(H_ROLES).split(","))
                        .map(s -> s.trim().toUpperCase()).filter(s -> !s.isEmpty()).collect(Collectors.toSet());
        CallerHolder.set(new Caller(req.getHeader(H_CUSTOMER), roles, correlationId));
        MDC.put("correlationId", correlationId);
        res.setHeader(H_CORRELATION, correlationId);
        try {
            chain.doFilter(req, res);
        } finally {
            CallerHolder.clear();
            MDC.remove("correlationId");
        }
    }

    /** Constant-time comparison so the token cannot be guessed via timing. */
    private boolean tokenMatches(String presented) {
        if (presented == null || props.serviceToken() == null) {
            return false;
        }
        return MessageDigest.isEqual(presented.getBytes(StandardCharsets.UTF_8),
                props.serviceToken().getBytes(StandardCharsets.UTF_8));
    }
}
