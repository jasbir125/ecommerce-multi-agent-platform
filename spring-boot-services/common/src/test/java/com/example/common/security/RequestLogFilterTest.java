package com.example.common.security;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

class RequestLogFilterTest {

    private final ListAppender<ILoggingEvent> appender = new ListAppender<>();
    private final Logger logger = (Logger) LoggerFactory.getLogger("http.access");

    @BeforeEach
    void attach() {
        appender.start();
        logger.addAppender(appender);
        logger.setLevel(Level.INFO);
    }

    @AfterEach
    void detach() {
        logger.detachAppender(appender);
    }

    private List<String> run(MockHttpServletRequest req, MockHttpServletResponse res) throws Exception {
        var filter = new RequestLogFilter(true, 500);
        filter.doFilter(req, res, new MockFilterChain() {
            @Override
            public void doFilter(jakarta.servlet.ServletRequest request, jakarta.servlet.ServletResponse response) throws java.io.IOException {
                request.getInputStream().readAllBytes();              // the controller reads the body
                request.setAttribute(RequestLogFilter.ATTR_CALLER, new Caller("CUST-1001", Set.of("CUSTOMER"), "ignored"));
                response.setContentType("application/json");
                response.getWriter().write("{\"workflowId\":\"WF-ABC123\",\"status\":\"STARTED\",\"token\":\"secret-value\"}");
            }
        });
        return appender.list.stream().map(ILoggingEvent::getFormattedMessage).toList();
    }

    @Test
    void logsInAndOutLinesWithCorrelationBodiesAndCaller() throws Exception {
        var req = new MockHttpServletRequest("POST", "/api/workflows");
        req.addHeader("X-Correlation-Id", "corr-123");
        req.setContentType("application/json");
        req.setContent("{\"orderId\":\"ORD-1\",\"password\":\"pw\"}".getBytes());
        var res = new MockHttpServletResponse();

        List<String> lines = run(req, res);

        assertEquals(2, lines.size());
        assertTrue(lines.get(0).startsWith("-> POST /api/workflows") && lines.get(0).contains("corr=corr-123"));
        String out = lines.get(1);
        assertTrue(out.startsWith("<- POST /api/workflows  200"), out);
        assertTrue(out.contains("caller=customer=CUST-1001[CUSTOMER]"), out);
        assertTrue(out.contains("corr=corr-123") && out.contains("wf=WF-ABC123"), out);
        assertTrue(out.contains("ORD-1") && out.contains("STARTED"), out);
        assertTrue(!out.contains("secret-value") && !out.contains("\"pw\""), out);
        assertEquals("corr-123", res.getHeader("X-Correlation-Id"));
        assertTrue(res.getContentAsString().contains("STARTED"), "response must still reach the client");
    }

    @Test
    void createsACorrelationIdWhenTheCallerSendsNone() throws Exception {
        var res = new MockHttpServletResponse();
        run(new MockHttpServletRequest("GET", "/api/orders/ORD-1"), res);
        assertNotNull(res.getHeader("X-Correlation-Id"));
        assertTrue(res.getHeader("X-Correlation-Id").length() >= 32);
    }
}
