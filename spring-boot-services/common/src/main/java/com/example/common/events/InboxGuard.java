package com.example.common.events;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/** Idempotent-consumer helper: the first caller for (consumer, eventId) wins. */
@Component
public class InboxGuard {

    private final JdbcClient jdbc;

    public InboxGuard(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** @return true if this event has not been processed by this consumer before. Call inside the handler's transaction. */
    public boolean firstTime(String consumer, String eventId) {
        return jdbc.sql("INSERT INTO processed_event (consumer, event_id) VALUES (?, ?) ON CONFLICT DO NOTHING")
                .params(consumer, eventId).update() == 1;
    }
}
