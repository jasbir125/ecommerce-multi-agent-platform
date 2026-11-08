package com.example.common.security;

import com.example.common.PlatformProperties;
import io.micrometer.core.instrument.MeterRegistry;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
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
 * Authenticates every request: a trusted caller presents X-Service-Token and says which customer (X-Customer-Id) and roles
 * (X-Roles) it acts for. Actuator endpoints are open. Also sets the correlation id for the request.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 10)
public class CallerFilter extends OncePerRequestFilter {

    public static final String H_TOKEN = "X-Service-Token";
    public static final String H_CUSTOMER = "X-Customer-Id";
    public static final String H_ROLES = "X-Roles";
    public static final String H_CORRELATION = "X-Correlation-Id";

    private final PlatformProperties props;
    private final MeterRegistry metrics;

    public CallerFilter(PlatformProperties props, MeterRegistry metrics) {
        this.props = props;
        this.metrics = metrics;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getRequestURI();
        return path.startsWith("/actuator");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain)
            throws ServletException, IOException {
        String correlationId = (String) req.getAttribute(RequestLogFilter.ATTR_CORRELATION);
        if (correlationId == null) {
            correlationId = req.getHeader(H_CORRELATION);
        }
        if (correlationId == null || correlationId.isBlank() || correlationId.length() > 100) {
            correlationId = UUID.randomUUID().toString();
        }
        if (props.securityEnabled() && !tokenMatches(req.getHeader(H_TOKEN))) {
            reject(res, req.getHeader(H_TOKEN) == null ? "AUTHENTICATION_REQUIRED" : "INVALID_SERVICE_TOKEN", "unauthorized");
            return;
        }
        Set<String> roles = req.getHeader(H_ROLES) == null ? Set.of()
                : Arrays.stream(req.getHeader(H_ROLES).split(","))
                        .map(r -> r.trim().toUpperCase()).filter(r -> !r.isEmpty()).collect(Collectors.toSet());
        Caller caller = new Caller(req.getHeader(H_CUSTOMER), roles, correlationId);
        CallerHolder.set(caller);
        req.setAttribute(RequestLogFilter.ATTR_CALLER, caller);
        MDC.put("correlationId", correlationId);
        res.setHeader(H_CORRELATION, correlationId);
        try {
            chain.doFilter(req, res);
        } finally {
            CallerHolder.clear();
            MDC.remove("correlationId");
        }
    }

    private void reject(HttpServletResponse res, String code, String title) throws IOException {
        metrics.counter("platform.auth.failures", "reason", code).increment();
        res.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        res.setContentType("application/problem+json");
        res.getWriter().write("{\"title\":\"" + title + "\",\"status\":401,\"code\":\"" + code + "\"}");
    }

    /** Constant-time comparison so the token cannot be guessed via timing. */
    private boolean tokenMatches(String presented) {
        if (presented == null || props.serviceToken() == null) {
            return false;
        }
        return MessageDigest.isEqual(presented.getBytes(StandardCharsets.UTF_8), props.serviceToken().getBytes(StandardCharsets.UTF_8));
    }
}
