package com.example.common;

import java.util.List;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * Cross-service settings (prefix "platform").
 *
 * @param serviceToken    shared secret trusted platform components present in X-Service-Token (service-to-service)
 * @param securityEnabled turn off only for isolated unit tests
 * @param auditServiceUrl where to send audit events; blank = log only (used by audit-service itself)
 * @param env             "dev" or "production"; production refuses to start with the demo token and without JWT
 * @param maxBodyBytes    largest request body accepted
 * @param publicPaths     paths that need no authentication (e.g. the token endpoint)
 * @param jwt             end-user token verification (RS256 via a JWKS endpoint); blank jwksUrl = disabled
 */
@ConfigurationProperties(prefix = "platform")
public record PlatformProperties(String serviceToken, boolean securityEnabled, String auditServiceUrl,
                                 @DefaultValue("dev") String env, @DefaultValue("65536") long maxBodyBytes,
                                 @DefaultValue List<String> publicPaths, @DefaultValue Jwt jwt, @DefaultValue Faults faults) {

    /** Test-only fault injection; must stay off outside local evaluation runs. */
    public record Faults(boolean enabled) {
    }

    public record Jwt(String jwksUrl, String issuer, String audience) {
        public boolean enabled() {
            return jwksUrl != null && !jwksUrl.isBlank();
        }
    }
}
