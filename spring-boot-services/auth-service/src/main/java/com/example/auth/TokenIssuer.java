package com.example.auth;

import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.RSASSASigner;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jose.jwk.gen.RSAKeyGenerator;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import java.security.KeyFactory;
import java.security.interfaces.RSAPrivateCrtKey;
import java.security.spec.PKCS8EncodedKeySpec;
import java.security.spec.RSAPublicKeySpec;
import java.time.Instant;
import java.util.Base64;
import java.util.Date;
import java.util.List;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/** Signs access tokens (RS256) and publishes the matching public key as a JWKS document. */
@Component
public class TokenIssuer {

    private static final Logger log = LoggerFactory.getLogger(TokenIssuer.class);

    private final AuthProperties props;
    private final RSAKey key;

    public TokenIssuer(AuthProperties props) throws Exception {
        this.props = props;
        if (props.privateKeyPem() != null && !props.privateKeyPem().isBlank()) {
            this.key = fromPem(props.privateKeyPem());
            log.info("Loaded signing key from configuration (kid={})", key.getKeyID());
        } else {
            this.key = new RSAKeyGenerator(2048).keyID(UUID.randomUUID().toString()).generate();
            log.warn("Generated an ephemeral signing key (kid={}): tokens become invalid when this service restarts. "
                    + "Set auth.private-key-pem for a stable key.", key.getKeyID());
        }
    }

    private static RSAKey fromPem(String pem) throws Exception {
        byte[] der = Base64.getMimeDecoder().decode(pem.replaceAll("-----[A-Z ]+-----", "").replaceAll("\\s", ""));
        var kf = KeyFactory.getInstance("RSA");
        var priv = (RSAPrivateCrtKey) kf.generatePrivate(new PKCS8EncodedKeySpec(der));
        var pub = kf.generatePublic(new RSAPublicKeySpec(priv.getModulus(), priv.getPublicExponent()));
        return new RSAKey.Builder((java.security.interfaces.RSAPublicKey) pub).privateKey(priv)
                .keyID(UUID.nameUUIDFromBytes(priv.getModulus().toByteArray()).toString()).build();
    }

    public record Issued(String token, long expiresInSeconds) {
    }

    public Issued issue(String username, String customerId, List<String> roles) throws Exception {
        Instant now = Instant.now();
        Instant exp = now.plusSeconds(props.ttlMinutes() * 60);
        JWTClaimsSet.Builder claims = new JWTClaimsSet.Builder().issuer(props.issuer()).audience(props.audience()).subject(username)
                .issueTime(Date.from(now)).notBeforeTime(Date.from(now)).expirationTime(Date.from(exp))
                .jwtID(UUID.randomUUID().toString()).claim("roles", roles);
        if (customerId != null) {
            claims.claim("customer_id", customerId);
        }
        SignedJWT jwt = new SignedJWT(new JWSHeader.Builder(JWSAlgorithm.RS256).keyID(key.getKeyID()).build(), claims.build());
        jwt.sign(new RSASSASigner(key));
        return new Issued(jwt.serialize(), props.ttlMinutes() * 60);
    }

    /** Public keys only. */
    public String jwks() {
        return new JWKSet(key.toPublicJWK()).toString();
    }
}
