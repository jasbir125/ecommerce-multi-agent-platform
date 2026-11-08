package com.example.common.events;

import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * Polls the outbox and publishes unsent rows. SKIP LOCKED lets several service
 * instances share the table without sending the same row twice at the same time.
 * Delivery is at-least-once, so consumers must be idempotent.
 */
@Component
@ConditionalOnProperty(name = "platform.messaging.enabled", havingValue = "true", matchIfMissing = true)
public class OutboxRelay {

    private static final Logger log = LoggerFactory.getLogger(OutboxRelay.class);

    private final JdbcClient jdbc;
    private final KafkaTemplate<String, String> kafka;

    public OutboxRelay(JdbcClient jdbc, KafkaTemplate<String, String> kafka, io.micrometer.core.instrument.MeterRegistry metrics) {
        this.jdbc = jdbc;
        this.kafka = kafka;
        // Events waiting to be published: should hover near zero; growth means Kafka or this service is unhealthy.
        io.micrometer.core.instrument.Gauge.builder("outbox.pending", () -> jdbc.sql("SELECT count(*) FROM outbox_event WHERE sent_at IS NULL").query(Long.class).single())
                .register(metrics);
    }

    @Scheduled(fixedDelayString = "${platform.outbox.poll-ms:500}")
    @Transactional
    public void relay() {
        List<Map<String, Object>> rows = jdbc.sql("""
                SELECT id, topic, event_key, payload, trace_context FROM outbox_event
                WHERE sent_at IS NULL ORDER BY id LIMIT 100 FOR UPDATE SKIP LOCKED""")
                .query().listOfRows();
        for (Map<String, Object> row : rows) {
            try {
                var record = new org.apache.kafka.clients.producer.ProducerRecord<String, String>(
                        (String) row.get("topic"), null, (String) row.get("event_key"), (String) row.get("payload"));
                if (row.get("trace_context") != null) {
                    record.headers().add("traceparent", ((String) row.get("trace_context")).getBytes(java.nio.charset.StandardCharsets.UTF_8));
                }
                var sent = kafka.send(record).get(5, TimeUnit.SECONDS);
                log.info("KAFKA OUT topic={} partition={} offset={} key={} payload={}", row.get("topic"),
                        sent.getRecordMetadata().partition(), sent.getRecordMetadata().offset(), row.get("event_key"),
                        com.example.common.util.LogSanitizer.clean((String) row.get("payload"), 1500));
                jdbc.sql("UPDATE outbox_event SET sent_at = now() WHERE id = ?").param(row.get("id")).update();
            } catch (Exception e) {
                // Leave the row unsent; it is retried on the next poll.
                log.warn("Outbox publish failed for id={} topic={}: {}", row.get("id"), row.get("topic"), e.getMessage());
                if (e instanceof InterruptedException) {
                    Thread.currentThread().interrupt();
                }
                return;
            }
        }
    }
}
