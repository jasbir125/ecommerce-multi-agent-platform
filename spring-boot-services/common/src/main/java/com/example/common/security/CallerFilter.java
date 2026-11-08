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
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Authenticates every request, one of two ways:
 * 1. "Authorization: Bearer <JWT>": an end user. Identity and roles come from the verified token only;
 *    X-Customer-Id / X-Roles headers are ignored, so a user cannot claim to be someone else.
 * 2. "X-Service-Token": a trusted platform component acting for a user; it supplies the identity headers.
 * Actuator and configured public paths (such as the token endpoint) are open.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 10)
public class CallerFilter extends OncePerRequestFilter {

    public static final String H_TOKEN = "X-Service-Token";
    public static final String H_CUSTOMER = "X-Customer-Id";
    public static final String H_ROLES = "X-Roles";
    public static final String H_CORRELATION = "X-Correlation-Id";
    private static final Logger log = LoggerFactory.getLogger(CallerFilter.class);

    private final PlatformProperties props;
    private final JwtVerifier jwt;
    private final MeterRegistry metrics;

    public CallerFilter(PlatformProperties props, JwtVerifier jwt, MeterRegistry metrics) {
        this.props = props;
        this.jwt = jwt;
        this.metrics = metrics;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getRequestURI();
        return path.startsWith("/actuator") || props.publicPaths().contains(path);
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
        Caller caller;
        String bearer = bearer(req);
        if (bearer != null) {
            try {
                var v = jwt.verify(bearer);
                caller = new Caller(v.customerId(), v.roles(), correlationId, true, v.subject());
            } catch (Exception e) {
                log.warn("rejected bearer token: {}", e.getMessage());
                reject(res, "INVALID_TOKEN", "unauthorized");
                return;
            }
        } else if (!props.securityEnabled() || tokenMatches(req.getHeader(H_TOKEN))) {
            Set<String> roles = req.getHeader(H_ROLES) == null ? Set.of()
                    : Arrays.stream(req.getHeader(H_ROLES).split(","))
                            .map(s -> s.trim().toUpperCase()).filter(s -> !s.isEmpty()).collect(Collectors.toSet());
            caller = new Caller(req.getHeader(H_CUSTOMER), roles, correlationId, false, null);
        } else {
            reject(res, req.getHeader(H_TOKEN) == null ? "AUTHENTICATION_REQUIRED" : "INVALID_SERVICE_TOKEN", "unauthorized");
            return;
        }
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
        res.setHeader("WWW-Authenticate", "Bearer");
        res.getWriter().write("{\"title\":\"" + title + "\",\"status\":401,\"code\":\"" + code + "\"}");
    }

    private static String bearer(HttpServletRequest req) {
        String h = req.getHeader("Authorization");
        return h != null && h.regionMatches(true, 0, "Bearer ", 0, 7) ? h.substring(7).trim() : null;
    }

    /** Constant-time comparison so the token cannot be guessed via timing. */
    private boolean tokenMatches(String presented) {
        if (presented == null || props.serviceToken() == null) {
            return false;
        }
        return MessageDigest.isEqual(presented.getBytes(StandardCharsets.UTF_8), props.serviceToken().getBytes(StandardCharsets.UTF_8));
    }
}
