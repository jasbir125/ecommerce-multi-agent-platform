package com.example.common.security;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.example.common.PlatformProperties;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

class CallerFilterTest {

    private final CallerFilter filter = new CallerFilter(new PlatformProperties("secret-token-1234", true, ""), new SimpleMeterRegistry());

    private MockHttpServletResponse call(MockHttpServletRequest req, AtomicReference<Caller> seen) throws Exception {
        var res = new MockHttpServletResponse();
        filter.doFilter(req, res, new MockFilterChain() {
            @Override
            public void doFilter(jakarta.servlet.ServletRequest rq, jakarta.servlet.ServletResponse rs) {
                seen.set(CallerHolder.current());
            }
        });
        return res;
    }

    @Test
    void validTokenSetsCustomerRolesAndCorrelationId() throws Exception {
        var req = new MockHttpServletRequest("GET", "/api/orders/ORD-1");
        req.addHeader("X-Service-Token", "secret-token-1234");
        req.addHeader("X-Customer-Id", "CUST-1001");
        req.addHeader("X-Roles", "manager, operator");
        req.addHeader("X-Correlation-Id", "corr-1");
        var seen = new AtomicReference<Caller>();

        var res = call(req, seen);

        assertEquals(200, res.getStatus());
        assertEquals("CUST-1001", seen.get().customerId());
        assertTrue(seen.get().hasRole("MANAGER") && seen.get().hasRole("OPERATOR"));
        assertEquals("corr-1", seen.get().correlationId());
        assertEquals("corr-1", res.getHeader("X-Correlation-Id"));
    }

    @Test
    void missingOrWrongTokenIsRefusedWith401() throws Exception {
        for (String token : new String[]{null, "wrong"}) {
            var req = new MockHttpServletRequest("GET", "/api/orders/ORD-1");
            if (token != null) {
                req.addHeader("X-Service-Token", token);
            }
            var seen = new AtomicReference<Caller>();
            var res = call(req, seen);
            assertEquals(401, res.getStatus());
            assertNull(seen.get(), "the request must not reach the controller");
        }
    }

    @Test
    void actuatorIsOpen() throws Exception {
        var seen = new AtomicReference<Caller>();
        assertEquals(200, call(new MockHttpServletRequest("GET", "/actuator/health"), seen).getStatus());
    }
}
