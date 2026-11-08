package com.example.common.events;

import org.apache.kafka.common.TopicPartition;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.listener.CommonErrorHandler;
import org.springframework.kafka.listener.DeadLetterPublishingRecoverer;
import org.springframework.kafka.listener.DefaultErrorHandler;
import org.springframework.util.backoff.ExponentialBackOff;

/**
 * Failed messages are retried with exponential backoff (1s, 2s, 4s...) and then
 * parked on "<topic>.dlt" so one poison message cannot block the partition.
 */
@Configuration
public class KafkaErrorConfig {

    @Bean
    CommonErrorHandler kafkaErrorHandler(KafkaTemplate<String, String> template) {
        // DLT topics have a single partition (see infra/kafka/create-topics.sh).
        var recoverer = new DeadLetterPublishingRecoverer(template,
                (record, ex) -> new TopicPartition(record.topic() + ".dlt", 0));
        var backoff = new ExponentialBackOff(1000L, 2.0);
        backoff.setMaxElapsedTime(15_000L);
        return new DefaultErrorHandler(recoverer, backoff);
    }
}
