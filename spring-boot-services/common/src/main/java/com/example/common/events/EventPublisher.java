package com.example.common.events;

import com.example.common.security.CallerHolder;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * Transactional outbox writer. Call inside the same @Transactional method that
 * changes business state: the event row commits (or rolls back) with it, and
 * {@link OutboxRelay} delivers it to Kafka afterwards.
 */
@Component
@ConditionalOnProperty(name = "platform.messaging.enabled", havingValue = "true", matchIfMissing = true)
public class EventPublisher {

    private final JdbcClient jdbc;
    private final ObjectMapper mapper;
    private final io.micrometer.tracing.Tracer tracer;

    public EventPublisher(JdbcClient jdbc, ObjectMapper mapper, io.micrometer.tracing.Tracer tracer) {
        this.jdbc = jdbc;
        this.mapper = mapper;
        this.tracer = tracer;
    }

    private String traceparent() {
        var span = tracer.currentSpan();
        return span == null ? null : "00-" + span.context().traceId() + "-" + span.context().spanId() + "-01";
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public String publish(String topic, String key, String type, String workflowId,
                          String causationId, Map<String, Object> payload) {
        String eventId = UUID.randomUUID().toString();
        String correlation = CallerHolder.current().correlationId();
        EventEnvelope env = new EventEnvelope(eventId, type, workflowId,
                correlation != null ? correlation : workflowId, causationId, Instant.now(), payload);
        try {
            jdbc.sql("INSERT INTO outbox_event (event_id, topic, event_key, payload, trace_context) VALUES (?, ?, ?, ?, ?)")
                    .params(eventId, topic, key, mapper.writeValueAsString(env), traceparent()).update();
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("Cannot serialize event " + type, e);
        }
        return eventId;
    }
}
