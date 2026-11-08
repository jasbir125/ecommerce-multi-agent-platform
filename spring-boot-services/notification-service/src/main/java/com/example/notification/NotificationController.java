package com.example.notification;

import com.example.notification.NotificationRecords.*;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/notifications")
public class NotificationController {

    private final NotificationService service;

    public NotificationController(NotificationService service) {
        this.service = service;
    }

    @PostMapping("/customer")
    public Result customer(@Valid @RequestBody CustomerNotification request) {
        return service.sendToCustomer(request);
    }

    @PostMapping("/internal")
    public Result internal(@Valid @RequestBody InternalNotification request) {
        return service.sendInternal(request);
    }
}
