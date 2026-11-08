package com.example.auth;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.example.common.PlatformProperties;
import com.example.common.security.CallerFilter;
import com.example.common.security.CallerHolder;
import com.example.common.security.JwtVerifier;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.MACSigner;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.PlainJWT;
import com.nimbusds.jwt.SignedJWT;
import com.sun.net.httpserver.HttpServer;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import jakarta.servlet.FilterChain;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Date;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

/** Token issuing and verification, end to end: real keys, a real JWKS endpoint, the real filter. */
class TokenSecurityTest {

    static final String ISS = "order-recovery-auth", AUD = "order-recovery-api", SERVICE_TOKEN = "local-demo-token";
    static HttpServer jwks;
    static TokenIssuer issuer;
    static JwtVerifier verifier;
    static PlatformProperties props;

    static AuthProperties authProps(long ttl) {
        return new AuthProperties(ISS, AUD, ttl, null, List.of());
    }

    @BeforeAll
    static void start() throws Exception {
        issuer = new TokenIssuer(authProps(15));
        jwks = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        jwks.createContext("/jwks", ex -> {
            byte[] b = issuer.jwks().getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().add("Content-Type", "application/json");
            ex.sendResponseHeaders(200, b.length);
            ex.getResponseBody().write(b);
            ex.close();
        });
        jwks.start();
        props = new PlatformProperties(SERVICE_TOKEN, true, "", "dev", 65536, List.of(),
                new PlatformProperties.Jwt("http://127.0.0.1:" + jwks.getAddress().getPort() + "/jwks", ISS, AUD), new PlatformProperties.Faults(false));
        verifier = new JwtVerifier(props);
    }

    @AfterAll
    static void stop() {
        jwks.stop(0);
    }

    private String token(String user, String customer, List<String> roles) throws Exception {
        return issuer.issue(user, customer, roles).token();
    }

    @Test
    void aValidTokenCarriesIdentityAndRoles() throws Exception {
        var v = verifier.verify(token("alice", "CUST-1001", List.of("customer")));
        assertThat(v.subject()).isEqualTo("alice");
        assertThat(v.customerId()).isEqualTo("CUST-1001");
        assertThat(v.roles()).containsExactly("CUSTOMER");
    }

    @Test
    void anExpiredTokenIsRejected() throws Exception {
        String expired = new TokenIssuer(authProps(-5)).issue("alice", "CUST-1001", List.of()).token();
        assertThatThrownBy(() -> verifier.verify(expired)).isInstanceOf(Exception.class);
    }

    @Test
    void aTokenSignedByAnotherKeyIsRejected() throws Exception {
        String forged = new TokenIssuer(authProps(15)).issue("mallory", "CUST-1001", List.of("MANAGER")).token();   // different key pair
        assertThatThrownBy(() -> verifier.verify(forged)).isInstanceOf(Exception.class);
    }

    @Test
    void tamperedClaimsInvalidateTheSignature() throws Exception {
        String[] parts = token("alice", "CUST-1001", List.of("CUSTOMER")).split("\\.");
        String evilPayload = java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(
                new String(java.util.Base64.getUrlDecoder().decode(parts[1]), StandardCharsets.UTF_8)
                        .replace("CUSTOMER", "MANAGER").getBytes(StandardCharsets.UTF_8));
        assertThatThrownBy(() -> verifier.verify(parts[0] + "." + evilPayload + "." + parts[2])).isInstanceOf(Exception.class);
    }

    @Test
    void anUnsignedAlgNoneTokenIsRejected() {
        String none = new PlainJWT(new JWTClaimsSet.Builder().issuer(ISS).audience(AUD).subject("mallory")
                .expirationTime(new Date(System.currentTimeMillis() + 60_000)).issueTime(new Date()).claim("roles", List.of("ADMIN")).build()).serialize();
        assertThatThrownBy(() -> verifier.verify(none)).isInstanceOf(Exception.class);
    }

    @Test
    void anHmacTokenSignedWithThePublicKeyAsSecretIsRejected() throws Exception {
        var claims = new JWTClaimsSet.Builder().issuer(ISS).audience(AUD).subject("mallory").issueTime(new Date())
                .expirationTime(new Date(System.currentTimeMillis() + 60_000)).claim("roles", List.of("ADMIN")).build();
        SignedJWT hs = new SignedJWT(new JWSHeader(JWSAlgorithm.HS256), claims);
        hs.sign(new MACSigner(issuer.jwks().getBytes(StandardCharsets.UTF_8).length >= 32 ? issuer.jwks().substring(0, 64).getBytes(StandardCharsets.UTF_8) : new byte[32]));
        assertThatThrownBy(() -> verifier.verify(hs.serialize())).isInstanceOf(Exception.class);
    }

    @Test
    void wrongAudienceOrIssuerIsRejected() throws Exception {
        assertThatThrownBy(() -> verifier.verify(new TokenIssuer(new AuthProperties(ISS, "some-other-api", 15, null, List.of()))
                .issue("a", "CUST-1", List.of()).token())).isInstanceOf(Exception.class);
        assertThatThrownBy(() -> verifier.verify(new TokenIssuer(new AuthProperties("evil-issuer", AUD, 15, null, List.of()))
                .issue("a", "CUST-1", List.of()).token())).isInstanceOf(Exception.class);
    }

    @Test
    void garbageIsRejected() {
        assertThatThrownBy(() -> verifier.verify("not.a.jwt")).isInstanceOf(Exception.class);
        assertThatThrownBy(() -> verifier.verify("")).isInstanceOf(Exception.class);
    }

    // ------------------------------------------------------------------ the filter
    private MockHttpServletResponse run(MockHttpServletRequest req, AtomicReference<com.example.common.security.Caller> seen) throws Exception {
        var res = new MockHttpServletResponse();
        FilterChain chain = (rq, rs) -> seen.set(CallerHolder.current());
        new CallerFilter(props, verifier, new SimpleMeterRegistry()).doFilter(req, res, chain);
        return res;
    }

    @Test
    void forgedIdentityHeadersAreIgnoredWhenAUserTokenIsPresent() throws Exception {
        var req = new MockHttpServletRequest("GET", "/api/orders/ORD-1");
        req.addHeader("Authorization", "Bearer " + token("alice", "CUST-1001", List.of("CUSTOMER")));
        req.addHeader("X-Customer-Id", "CUST-2002");              // trying to be someone else
        req.addHeader("X-Roles", "MANAGER,ADMIN");                // trying to gain roles
        var seen = new AtomicReference<com.example.common.security.Caller>();
        var res = run(req, seen);
        assertThat(res.getStatus()).isEqualTo(200);
        assertThat(seen.get().customerId()).isEqualTo("CUST-1001");
        assertThat(seen.get().roles()).containsExactly("CUSTOMER");
        assertThat(seen.get().jwt()).isTrue();
    }

    @Test
    void aBadBearerTokenIsRefusedEvenWithAValidServiceToken() throws Exception {
        var req = new MockHttpServletRequest("GET", "/api/orders/ORD-1");
        req.addHeader("Authorization", "Bearer garbage");
        req.addHeader("X-Service-Token", SERVICE_TOKEN);
        var res = run(req, new AtomicReference<>());
        assertThat(res.getStatus()).isEqualTo(401);
        assertThat(res.getContentAsString()).contains("INVALID_TOKEN");
    }

    @Test
    void trustedComponentsStillActForUsersWithTheServiceToken() throws Exception {
        var req = new MockHttpServletRequest("GET", "/api/orders/ORD-1");
        req.addHeader("X-Service-Token", SERVICE_TOKEN);
        req.addHeader("X-Customer-Id", "CUST-1001");
        req.addHeader("X-Roles", "agent");
        var seen = new AtomicReference<com.example.common.security.Caller>();
        assertThat(run(req, seen).getStatus()).isEqualTo(200);
        assertThat(seen.get().jwt()).isFalse();
        assertThat(seen.get().customerId()).isEqualTo("CUST-1001");
        assertThat(seen.get().roles()).containsExactly("AGENT");
    }

    @Test
    void noCredentialsAndWrongServiceTokenAreRefused() throws Exception {
        assertThat(run(new MockHttpServletRequest("GET", "/api/orders/1"), new AtomicReference<>()).getStatus()).isEqualTo(401);
        var wrong = new MockHttpServletRequest("GET", "/api/orders/1");
        wrong.addHeader("X-Service-Token", "nope");
        var res = run(wrong, new AtomicReference<>());
        assertThat(res.getStatus()).isEqualTo(401);
        assertThat(res.getContentAsString()).contains("INVALID_SERVICE_TOKEN");
    }

    @Test
    void anEndUserTokenIsRefusedByInternalOnlyEndpoints() throws Exception {
        var req = new MockHttpServletRequest("POST", "/api/payments/refunds");
        req.addHeader("Authorization", "Bearer " + token("alice", "CUST-1001", List.of("CUSTOMER")));
        var res = new MockHttpServletResponse();
        FilterChain chain = (rq, rs) -> assertThatThrownBy(CallerHolder::requireInternal).hasMessageContaining("not available to end users");
        new CallerFilter(props, verifier, new SimpleMeterRegistry()).doFilter(req, res, chain);
    }
}
