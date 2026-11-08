package com.example.notification;

import com.example.common.events.EventEnvelope;
import com.example.common.events.EventLog;
import com.example.common.events.InboxGuard;
import com.example.notification.NotificationRecords.CustomerNotification;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.Map;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * Consumes customer.notification.requested. Duplicate deliveries are skipped via
 * the inbox table; failures are retried and then dead-lettered (see common KafkaErrorConfig).
 */
@Component
public class NotificationListener {

    private static final org.slf4j.Logger log = org.slf4j.LoggerFactory.getLogger(NotificationListener.class);

    private final NotificationService service;
    private final InboxGuard inbox;
    private final ObjectMapper mapper;

    public NotificationListener(NotificationService service, InboxGuard inbox, ObjectMapper mapper) {
        this.service = service;
        this.inbox = inbox;
        this.mapper = mapper;
    }

    @KafkaListener(topics = "customer.notification.requested")
    @Transactional
    @SuppressWarnings("unchecked")
    public void onMessage(String message) throws Exception {
        EventEnvelope env = mapper.readValue(message, EventEnvelope.class);
        try {
            EventLog.received(log, "notification-service", env, mapper);
            if (!inbox.firstTime("notification-service", env.eventId())) {
                EventLog.duplicate(log, "notification-service", env);
                return; // duplicate delivery
            }
            Map<String, Object> p = env.payload();
            service.sendToCustomer(new CustomerNotification(env.workflowId(), (String) p.get("customerId"),
                    (String) p.get("channel"), (String) p.get("template"), (Map<String, String>) p.get("params")));
        } finally {
            EventLog.done();
        }
    }
}
