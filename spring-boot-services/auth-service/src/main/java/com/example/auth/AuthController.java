package com.example.auth;

import com.example.common.web.ApiException;
import io.micrometer.core.instrument.MeterRegistry;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import java.time.Duration;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/**
 * POST /api/auth/token  (username + password) and GET /.well-known/jwks.json. Both are public.
 * Failed logins are rate limited per username and per client address, and an unknown user costs the same
 * time as a wrong password (no user enumeration by timing).
 */
@RestController
public class AuthController {

    public record LoginRequest(@NotBlank @Size(max = 100) String username, @NotBlank @Size(max = 200) String password) {
    }

    private static final Logger log = LoggerFactory.getLogger(AuthController.class);
    private static final int MAX_FAILURES = 5;
    private static final Duration WINDOW = Duration.ofMinutes(5);

    private final TokenIssuer issuer;
    private final Map<String, AuthProperties.User> users = new ConcurrentHashMap<>();
    private final Map<String, String> hashes = new ConcurrentHashMap<>();
    private final BCryptPasswordEncoder encoder = new BCryptPasswordEncoder(10);
    private final String dummyHash = encoder.encode("not-a-real-password");
    private final Map<String, Deque<Long>> failures = new ConcurrentHashMap<>();
    private final MeterRegistry metrics;

    public AuthController(TokenIssuer issuer, AuthProperties props, MeterRegistry metrics) {
        this.issuer = issuer;
        this.metrics = metrics;
        for (var u : props.users()) {
            users.put(u.username().toLowerCase(), u);
            hashes.put(u.username().toLowerCase(), encoder.encode(u.password()));
        }
    }

    @PostMapping(value = "/api/auth/token", produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> token(@Valid @RequestBody LoginRequest req, HttpServletRequest http) throws Exception {
        String name = req.username().toLowerCase();
        String client = http.getRemoteAddr();
        if (tooManyFailures(name) || tooManyFailures("ip:" + client)) {
            metrics.counter("auth.login", "result", "throttled").increment();
            throw new ApiException(HttpStatus.TOO_MANY_REQUESTS, "TOO_MANY_ATTEMPTS", "Too many failed attempts; try again in a few minutes");
        }
        var user = users.get(name);
        boolean ok = encoder.matches(req.password(), user == null ? dummyHash : hashes.get(name)) && user != null;
        if (!ok) {
            recordFailure(name);
            recordFailure("ip:" + client);
            metrics.counter("auth.login", "result", "failed").increment();
            log.warn("login failed for '{}' from {}", name.replaceAll("[\\r\\n]", ""), client);
            throw new ApiException(HttpStatus.UNAUTHORIZED, "INVALID_CREDENTIALS", "Invalid username or password");
        }
        failures.remove(name);
        var t = issuer.issue(user.username(), user.customerId(), user.roles());
        metrics.counter("auth.login", "result", "success").increment();
        return Map.of("accessToken", t.token(), "tokenType", "Bearer", "expiresIn", t.expiresInSeconds(),
                "username", user.username(), "customerId", user.customerId() == null ? "" : user.customerId(), "roles", user.roles());
    }

    @GetMapping(value = "/.well-known/jwks.json", produces = MediaType.APPLICATION_JSON_VALUE)
    public String jwks() {
        return issuer.jwks();
    }

    private boolean tooManyFailures(String key) {
        Deque<Long> q = failures.get(key);
        if (q == null) {
            return false;
        }
        synchronized (q) {
            long cutoff = System.currentTimeMillis() - WINDOW.toMillis();
            while (!q.isEmpty() && q.peekFirst() < cutoff) {
                q.pollFirst();
            }
            return q.size() >= MAX_FAILURES;
        }
    }

    private void recordFailure(String key) {
        Deque<Long> q = failures.computeIfAbsent(key, k -> new ArrayDeque<>());
        synchronized (q) {
            q.addLast(System.currentTimeMillis());
        }
    }
}
