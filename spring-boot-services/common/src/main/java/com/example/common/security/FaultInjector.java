package com.example.common.security;

import com.example.common.PlatformProperties;
import com.example.common.web.ApiException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/**
 * Lets the evaluation harness make a specific operation fail once (for example "the replacement order fails after
 * stock was reserved") so failure handling can be tested. Disabled unless platform.faults.enabled=true; when disabled
 * it never touches the database. Production mode refuses to start with it on.
 */
@Component
public class FaultInjector {

    private static final Logger log = LoggerFactory.getLogger(FaultInjector.class);

    private final boolean enabled;
    private final JdbcClient jdbc;

    public FaultInjector(PlatformProperties props, ObjectProvider<JdbcClient> jdbc) {
        this.enabled = props.faults().enabled();
        this.jdbc = jdbc.getIfAvailable();
        if (enabled) {
            log.warn("FAULT INJECTION IS ENABLED. This is for evaluation runs only.");
        }
    }

    /** Throws a 500 (mode=error) or 503 (mode=unavailable) once if a matching fault was registered. */
    public void check(String service, String operation, String scope) {
        if (!enabled || jdbc == null || scope == null) {
            return;
        }
        var rows = jdbc.sql("""
                UPDATE fault_injection SET remaining = remaining - 1
                WHERE id = (SELECT id FROM fault_injection WHERE service = :s AND operation = :o AND scope = :sc AND remaining > 0 ORDER BY id LIMIT 1)
                RETURNING mode""").param("s", service).param("o", operation).param("sc", scope).query(String.class).list();
        if (!rows.isEmpty()) {
            log.warn("injected fault: {}.{} scope={} mode={}", service, operation, scope, rows.get(0));
            if ("unavailable".equals(rows.get(0))) {
                throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "INJECTED_UNAVAILABLE", service + " is unavailable (injected)");
            }
            throw new ApiException(HttpStatus.INTERNAL_SERVER_ERROR, "INJECTED_FAULT", service + "." + operation + " failed (injected)");
        }
    }
}
