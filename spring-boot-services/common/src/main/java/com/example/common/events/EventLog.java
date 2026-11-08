package com.example.common.events;

import com.example.common.util.LogSanitizer;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.MDC;

/** Log lines for Kafka messages, in the same shape in every service: topic, ids and the (masked, truncated) payload. */
public final class EventLog {

    private static final int MAX = 1500;

    private EventLog() {
    }

    /** Call first in a listener: puts the event's correlation and workflow ids in the logging context and logs the message. */
    public static void received(Logger log, String consumer, EventEnvelope env, ObjectMapper mapper) {
        if (env.correlationId() != null) {
            MDC.put("correlationId", env.correlationId());
        }
        if (env.workflowId() != null) {
            MDC.put("workflowId", env.workflowId());
        }
        log.info("KAFKA IN  consumer={} type={} eventId={} corr={} wf={} cause={} payload={}", consumer, env.type(), env.eventId(),
                env.correlationId(), env.workflowId(), env.causationId(), payload(env, mapper));
    }

    public static void duplicate(Logger log, String consumer, EventEnvelope env) {
        log.info("KAFKA IN  consumer={} type={} eventId={} corr={} wf={} duplicate delivery, skipped", consumer, env.type(), env.eventId(),
                env.correlationId(), env.workflowId());
    }

    public static void done() {
        MDC.remove("correlationId");
        MDC.remove("workflowId");
    }

    static String payload(EventEnvelope env, ObjectMapper mapper) {
        try {
            return LogSanitizer.clean(mapper.writeValueAsString(env.payload()), MAX);
        } catch (Exception e) {
            return "<unprintable>";
        }
    }
}
