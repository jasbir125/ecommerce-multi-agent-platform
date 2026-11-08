package com.example.common.security;

import com.example.common.PlatformProperties;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.jwk.source.JWKSource;
import com.nimbusds.jose.jwk.source.JWKSourceBuilder;
import com.nimbusds.jose.proc.JWSVerificationKeySelector;
import com.nimbusds.jose.proc.SecurityContext;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.proc.ConfigurableJWTProcessor;
import com.nimbusds.jwt.proc.DefaultJWTClaimsVerifier;
import com.nimbusds.jwt.proc.DefaultJWTProcessor;
import java.net.MalformedURLException;
import java.net.URI;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;
import org.springframework.stereotype.Component;

/**
 * Verifies end-user access tokens: RS256 signature against the issuer's published keys (JWKS, cached and refreshed
 * when an unknown key id appears), plus issuer, audience, expiry and required claims. Only RS256 is accepted, so
 * "alg: none" and HMAC-confusion tokens are rejected.
 */
@Component
public class JwtVerifier {

    public record Verified(String subject, String customerId, Set<String> roles) {
    }

    private final ConfigurableJWTProcessor<SecurityContext> processor;
    private final boolean enabled;

    public JwtVerifier(PlatformProperties props) throws MalformedURLException {
        var jwt = props.jwt();
        this.enabled = jwt.enabled();
        if (!enabled) {
            this.processor = null;
            return;
        }
        JWKSource<SecurityContext> keys = JWKSourceBuilder.create(URI.create(jwt.jwksUrl()).toURL())
                .retrying(true).cache(300_000, 15_000).build();
        DefaultJWTProcessor<SecurityContext> p = new DefaultJWTProcessor<>();
        p.setJWSKeySelector(new JWSVerificationKeySelector<>(JWSAlgorithm.RS256, keys));
        p.setJWTClaimsSetVerifier(new DefaultJWTClaimsVerifier<>(jwt.audience(),
                new JWTClaimsSet.Builder().issuer(jwt.issuer()).build(), Set.of("sub", "exp", "iat")));
        this.processor = p;
    }

    public boolean enabled() {
        return enabled;
    }

    /** @throws Exception if the token is malformed, expired, signed by an unknown key, or has the wrong issuer/audience. */
    public Verified verify(String token) throws Exception {
        if (!enabled) {
            throw new IllegalStateException("JWT verification is not configured");
        }
        JWTClaimsSet c = processor.process(token, null);
        List<String> roles = c.getStringListClaim("roles");
        return new Verified(c.getSubject(), c.getStringClaim("customer_id"),
                roles == null ? Set.of() : roles.stream().map(String::toUpperCase).collect(Collectors.toSet()));
    }
}
