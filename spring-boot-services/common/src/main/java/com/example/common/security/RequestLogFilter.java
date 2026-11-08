package com.example.common.security;

import com.example.common.util.LogSanitizer;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.util.ContentCachingRequestWrapper;
import org.springframework.web.util.ContentCachingResponseWrapper;

/**
 * One "in" line and one "out" line for every API call, so a request can be followed through every service.
 *
 * <pre>
 *   -> POST /api/approvals/APR-1/decision  caller=jwt:manny[MANAGER,OPERATOR] corr=... wf=WF-...
 *   <- POST /api/approvals/APR-1/decision  200 18ms  req={...} resp={...}
 * </pre>
 *
 * The correlation id comes from the caller's X-Correlation-Id header (or is created here), is placed in the logging
 * context (MDC) so every log line of the request carries it, and is returned in the response header.
 * Bodies are masked and truncated (see {@link LogSanitizer}); authorization headers and tokens are never logged.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 5)
public class RequestLogFilter extends OncePerRequestFilter {

    public static final String ATTR_CORRELATION = "platform.correlationId";
    public static final String ATTR_CALLER = "platform.caller";
    public static final String H_WORKFLOW = "X-Workflow-Id";

    private static final Logger log = LoggerFactory.getLogger("http.access");
    private static final Pattern WORKFLOW_ID = Pattern.compile("WF-[A-Za-z0-9]+");
    private static final Pattern WORKFLOW_IN_BODY = Pattern.compile("\"workflowId\"\\s*:\\s*\"(WF-[A-Za-z0-9]+)\"");

    private final boolean bodies;
    private final int maxChars;

    public RequestLogFilter(@Value("${platform.logging.bodies:true}") boolean bodies,
                            @Value("${platform.logging.max-body-chars:2000}") int maxChars) {
        this.bodies = bodies;
        this.maxChars = maxChars;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String p = request.getRequestURI();
        return p.startsWith("/actuator") || p.equals("/metrics") || p.equals("/favicon.ico");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String corr = request.getHeader(CallerFilter.H_CORRELATION);
        if (corr == null || corr.isBlank() || corr.length() > 100) {
            corr = UUID.randomUUID().toString();
        }
        request.setAttribute(ATTR_CORRELATION, corr);
        response.setHeader(CallerFilter.H_CORRELATION, corr);

        String wf = request.getHeader(H_WORKFLOW);
        if (wf == null) {
            Matcher m = WORKFLOW_ID.matcher(request.getRequestURI());
            wf = m.find() ? m.group() : null;
        }
        putContext(corr, wf);

        String target = request.getMethod() + " " + request.getRequestURI()
                + (request.getQueryString() != null ? "?" + LogSanitizer.clean(request.getQueryString(), 200) : "");
        log.info("-> {}  from={} corr={} wf={}", target, request.getRemoteAddr(), corr, wf == null ? "-" : wf);

        var req = new ContentCachingRequestWrapper(request, Math.max(maxChars * 4, 8192));
        var res = new ContentCachingResponseWrapper(response);
        long t0 = System.nanoTime();
        Throwable failure = null;
        try {
            chain.doFilter(req, res);
        } catch (IOException | ServletException | RuntimeException e) {
            failure = e;
            throw e;
        } finally {
            long ms = (System.nanoTime() - t0) / 1_000_000;
            String reqBody = body(req.getContentAsByteArray(), req.getContentType());
            String resBody = body(res.getContentAsByteArray(), res.getContentType());
            if (wf == null) {
                wf = findWorkflow(reqBody, resBody);
            }
            putContext(corr, wf);                        // CallerFilter clears the context on its way out
            int status = failure != null ? 500 : res.getStatus();
            String line = "<- {}  {} {}ms caller={} corr={} wf={}" + (bodies ? "  req={}  resp={}" : "");
            Object[] args = bodies
                    ? new Object[]{target, status, ms, caller(request), corr, wf == null ? "-" : wf, reqBody, resBody}
                    : new Object[]{target, status, ms, caller(request), corr, wf == null ? "-" : wf};
            if (status >= 500) {
                log.error(line, args);
            } else if (status >= 400) {
                log.warn(line, args);
            } else {
                log.info(line, args);
            }
            res.copyBodyToResponse();
            MDC.remove("correlationId");
            MDC.remove("workflowId");
        }
    }

    private static void putContext(String corr, String wf) {
        MDC.put("correlationId", corr);
        if (wf != null) {
            MDC.put("workflowId", wf);
        }
    }

    private String body(byte[] bytes, String contentType) {
        if (!bodies || bytes == null || bytes.length == 0) {
            return "-";
        }
        if (contentType != null && !(contentType.contains("json") || contentType.contains("text") || contentType.contains("xml")
                || contentType.contains("x-www-form"))) {
            return "<" + contentType + ", " + bytes.length + " bytes>";
        }
        return LogSanitizer.clean(new String(bytes, StandardCharsets.UTF_8), maxChars);
    }

    private static String findWorkflow(String... texts) {
        for (String t : texts) {
            Matcher m = WORKFLOW_IN_BODY.matcher(t == null ? "" : t);
            if (m.find()) {
                return m.group(1);
            }
        }
        return null;
    }

    private static String caller(HttpServletRequest req) {
        Object c = req.getAttribute(ATTR_CALLER);
        if (!(c instanceof Caller caller)) {
            return "anonymous";
        }
        String roles = caller.roles().isEmpty() ? "" : caller.roles().stream().sorted().toList().toString();
        return caller.jwt()
                ? "jwt:" + caller.subject() + "(" + caller.customerId() + ")" + roles
                : "service(customer=" + caller.customerId() + ")" + roles;
    }
}
