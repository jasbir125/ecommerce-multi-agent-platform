package com.example.common;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Cross-service settings (prefix "platform").
 *
 * @param serviceToken       shared secret every caller must present in X-Service-Token
 * @param securityEnabled    turn off only for isolated unit tests
 * @param auditServiceUrl    where to send audit events; blank = log only (used by audit-service itself)
 */
@ConfigurationProperties(prefix = "platform")
public record PlatformProperties(String serviceToken, boolean securityEnabled, String auditServiceUrl) {
}
