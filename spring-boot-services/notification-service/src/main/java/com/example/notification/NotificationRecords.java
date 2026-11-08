package com.example.notification;

import jakarta.validation.constraints.NotBlank;
import java.util.Map;

public final class NotificationRecords {

    private NotificationRecords() {
    }

    /** params carry the verified facts (e.g. refundId); the text itself comes from a fixed template. */
    public record CustomerNotification(@NotBlank String workflowId, @NotBlank String customerId,
                                       String channel, @NotBlank String template, Map<String, String> params) {
    }

    public record InternalNotification(String workflowId, String channel, @NotBlank String message) {
    }

    public record Result(long notificationId, String status, String message) {
    }
}
