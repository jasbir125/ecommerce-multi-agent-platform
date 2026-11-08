package com.example.auth;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.example.common.web.ApiException;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;

class LoginTest {

    AuthController controller;

    @BeforeEach
    void setUp() throws Exception {
        var props = new AuthProperties("iss", "aud", 15, null, List.of(
                new AuthProperties.User("alice", "right-password", "CUST-1001", List.of("CUSTOMER")),
                new AuthProperties.User("manny", "mgr-password", null, List.of("MANAGER", "OPERATOR"))));
        controller = new AuthController(new TokenIssuer(props), props, new SimpleMeterRegistry());
    }

    private static MockHttpServletRequest from(String ip) {
        var r = new MockHttpServletRequest();
        r.setRemoteAddr(ip);
        return r;
    }

    @Test
    void correctCredentialsReturnATokenWithTheUsersIdentity() throws Exception {
        var out = controller.token(new AuthController.LoginRequest("Alice", "right-password"), from("10.0.0.1"));
        assertThat(out.get("tokenType")).isEqualTo("Bearer");
        assertThat(out.get("customerId")).isEqualTo("CUST-1001");
        assertThat(out.get("roles")).isEqualTo(List.of("CUSTOMER"));
        assertThat((String) out.get("accessToken")).hasSize(out.get("accessToken").toString().length()).contains(".");
        assertThat(out.get("expiresIn")).isEqualTo(900L);
    }

    @Test
    void wrongPasswordAndUnknownUserGiveTheSameError() {
        var wrong = catchApi(() -> controller.token(new AuthController.LoginRequest("alice", "bad"), from("10.0.0.2")));
        var unknown = catchApi(() -> controller.token(new AuthController.LoginRequest("nobody", "bad"), from("10.0.0.3")));
        assertThat(wrong.code()).isEqualTo("INVALID_CREDENTIALS").isEqualTo(unknown.code());
        assertThat(wrong.getMessage()).isEqualTo(unknown.getMessage());
    }

    @Test
    void repeatedFailuresAreThrottledEvenForTheRightPassword() throws Exception {
        for (int i = 0; i < 5; i++) {
            catchApi(() -> controller.token(new AuthController.LoginRequest("alice", "bad"), from("10.0.0.4")));
        }
        var blocked = catchApi(() -> controller.token(new AuthController.LoginRequest("alice", "right-password"), from("10.0.0.4")));
        assertThat(blocked.code()).isEqualTo("TOO_MANY_ATTEMPTS");
    }

    @Test
    void oneAddressCannotLockOutOtherUsersButIsThrottledItself() {
        for (int i = 0; i < 5; i++) {
            catchApi(() -> controller.token(new AuthController.LoginRequest("user" + System.nanoTime(), "bad"), from("10.9.9.9")));
        }
        assertThat(catchApi(() -> controller.token(new AuthController.LoginRequest("manny", "mgr-password"), from("10.9.9.9"))).code())
                .isEqualTo("TOO_MANY_ATTEMPTS");
        assertThat(catchApi(() -> controller.token(new AuthController.LoginRequest("alice", "bad"), from("10.1.1.1"))).code())
                .isEqualTo("INVALID_CREDENTIALS");
    }

    @Test
    void jwksContainsOnlyPublicKeyMaterial() {
        String jwks = controller.jwks();
        assertThat(jwks).contains("\"kty\":\"RSA\"").contains("\"kid\"").doesNotContain("\"d\"").doesNotContain("\"p\"").doesNotContain("\"q\"");
    }

    private static ApiException catchApi(ThrowingCall c) {
        try {
            c.run();
        } catch (ApiException e) {
            return e;
        } catch (Exception e) {
            throw new AssertionError(e);
        }
        throw new AssertionError("expected an ApiException");
    }

    interface ThrowingCall {
        void run() throws Exception;
    }
}
