package com.example.policy;

import com.example.policy.PolicyRecords.IngestRequest;
import java.time.LocalDate;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/** Loads demo policies on first start. Newer refund policy v2 supersedes v1. */
@Component
public class PolicySeeder implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(PolicySeeder.class);

    private final IngestionService ingestion;
    private final JdbcClient jdbc;
    private final boolean enabled;

    public PolicySeeder(IngestionService ingestion, JdbcClient jdbc, @Value("${policy.seed-on-startup}") boolean enabled) {
        this.ingestion = ingestion;
        this.jdbc = jdbc;
        this.enabled = enabled;
    }

    @Override
    public void run(ApplicationArguments args) {
        if (!enabled || jdbc.sql("SELECT count(*) FROM policy_document").query(Long.class).single() > 0) {
            return;
        }
        try {
            ingestion.ingest(new IngestRequest("refund-policy", "1", "Refund Policy (legacy)", """
                    Customers may request a refund within 7 days of delivery for items that are unused.

                    Delayed shipments are not covered by this legacy policy. Refunds are issued to the original payment method.""",
                    "ALL", "refund", LocalDate.of(2024, 1, 1), LocalDate.of(2025, 12, 31)));
            ingestion.ingest(new IngestRequest("refund-policy", "2", "Refund Policy", """
                    Orders delayed by three (3) or more days beyond the promised delivery date are eligible for a full refund of the order amount.

                    Shipments confirmed lost by the carrier are eligible for a full refund. The refund can never exceed the amount paid for the order.

                    Every refund must be approved by a manager before it is issued. Refunds are returned to the original payment method.

                    An order that has already shipped cannot be cancelled directly; the refund is processed after the carrier exception case is opened.""",
                    "ALL", "refund", LocalDate.of(2026, 1, 1), null));
            ingestion.ingest(new IngestRequest("shipping-delay-policy", "1", "Shipping Delay Policy", """
                    A shipment is considered delayed when it has not arrived three days after the promised delivery date.

                    When a shipment shows no carrier scan for five days it is treated as lost and a carrier exception case must be opened once per shipment.

                    Customers are offered a refund or a replacement when a shipment is delayed or lost.""",
                    "ALL", "shipping", LocalDate.of(2026, 1, 1), null));
            ingestion.ingest(new IngestRequest("price-protection-policy", "1", "Price Protection and Replacement Policy", """
                    When a replacement is offered for a delayed or lost order, the suggested replacement should cost the same or less than the original item.

                    Replacements must be in stock and are reserved before the replacement order is created.""",
                    "ALL", "replacement", LocalDate.of(2026, 1, 1), null));
            log.info("Seeded demo policies");
        } catch (RuntimeException e) {
            log.error("Policy seeding failed (is the embedding provider reachable?): {}", e.getMessage());
        }
    }
}
