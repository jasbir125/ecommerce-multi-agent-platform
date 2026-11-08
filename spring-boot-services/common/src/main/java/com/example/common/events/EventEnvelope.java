package com.example.common.events;

import java.time.Instant;
import java.util.Map;

/**
 * Standard wrapper for every Kafka message.
 *
 * @param eventId       unique id; consumers use it to ignore duplicates
 * @param correlationId ties together everything caused by one customer request
 * @param causationId   id of the event/command that directly caused this one
 */
public record EventEnvelope(String eventId, String type, String workflowId, String correlationId,
                            String causationId, Instant occurredAt, Map<String, Object> payload) {
}
