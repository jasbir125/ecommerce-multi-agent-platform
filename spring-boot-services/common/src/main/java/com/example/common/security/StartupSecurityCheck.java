package com.example.common.security;

import com.example.common.PlatformProperties;
import jakarta.annotation.PostConstruct;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/** In production mode the service refuses to start with the demo token, a weak token, or without user-token verification. */
@Component
public class StartupSecurityCheck {

    private static final Logger log = LoggerFactory.getLogger(StartupSecurityCheck.class);
    static final String DEMO_TOKEN = "local-demo-token";

    private final PlatformProperties props;

    public StartupSecurityCheck(PlatformProperties props) {
        this.props = props;
    }

    @PostConstruct
    void check() {
        boolean weak = props.serviceToken() == null || DEMO_TOKEN.equals(props.serviceToken()) || props.serviceToken().length() < 32;
        if ("production".equalsIgnoreCase(props.env())) {
            if (weak) {
                throw new IllegalStateException("platform.env=production but the service token is the demo value or shorter than 32 characters");
            }
            if (props.faults().enabled()) {
                throw new IllegalStateException("platform.env=production must not enable fault injection");
            }
            if (!props.securityEnabled()) {
                throw new IllegalStateException("platform.env=production requires platform.security-enabled=true");
            }
            if (!props.jwt().enabled() && !props.publicPaths().contains("/api/auth/token")) {
                throw new IllegalStateException("platform.env=production requires platform.jwt.jwks-url (end-user token verification)");
            }
        } else if (weak) {
            log.warn("SECURITY: running with the demo/weak service token (platform.env={}). Never deploy like this.", props.env());
        }
    }
}
