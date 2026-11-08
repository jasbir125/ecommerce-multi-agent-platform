package com.example.auth;

import java.util.List;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * @param issuer         "iss" claim; services must be configured with the same value
 * @param audience       "aud" claim
 * @param ttlMinutes     access token lifetime (short on purpose; there are no refresh tokens)
 * @param privateKeyPem  optional PKCS#8 PEM so keys survive restarts; blank = generate at startup
 * @param users          demo users. Passwords are hashed with BCrypt at startup.
 */
@ConfigurationProperties(prefix = "auth")
public record AuthProperties(@DefaultValue("order-recovery-auth") String issuer,
                             @DefaultValue("order-recovery-api") String audience,
                             @DefaultValue("15") long ttlMinutes, String privateKeyPem, @DefaultValue List<User> users) {

    public record User(String username, String password, String customerId, List<String> roles) {
    }
}
