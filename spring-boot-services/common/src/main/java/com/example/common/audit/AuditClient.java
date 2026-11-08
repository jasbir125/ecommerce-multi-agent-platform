package com.example.common.audit;

import com.example.common.PlatformProperties;
import com.example.common.security.Caller;
import com.example.common.security.CallerHolder;
import com.example.common.web.PlatformRestClient;
import java.util.HashMap;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

/**
 * Sends audit events to audit-service. Auditing must never break a business
 * call, so failures are logged and swallowed. With no audit URL configured
 * (audit-service itself) it only logs.
 */
@Component
public class AuditClient {

    private static final Logger log = LoggerFactory.getLogger(AuditClient.class);

    private final RestClient client;

    public AuditClient(PlatformProperties props, PlatformRestClient factory) {
        String url = props.auditServiceUrl();
        this.client = (url == null || url.isBlank()) ? null : factory.forBaseUrl(url);
    }

    public void record(String workflowId, String agentName, String eventType, String actor, Map<String, Object> payload) {
        Caller caller = CallerHolder.current();
        Map<String, Object> body = new HashMap<>();
        body.put("workflowId", workflowId);
        body.put("agentName", agentName);
        body.put("eventType", eventType);
        body.put("actor", actor);
        body.put("correlationId", caller.correlationId());
        body.put("payload", payload == null ? Map.of() : payload);
        if (client == null) {
            log.info("AUDIT {} workflow={} actor={}", eventType, workflowId, actor);
            return;
        }
        try {
            client.post().uri("/api/audit/events").body(body).retrieve().toBodilessEntity();
        } catch (Exception e) {
            log.warn("Audit delivery failed for {} (workflow {}): {}", eventType, workflowId, e.getMessage());
        }
    }
}
