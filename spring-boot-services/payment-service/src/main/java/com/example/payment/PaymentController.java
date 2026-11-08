package com.example.payment;

import com.example.common.security.CallerHolder;
import com.example.payment.PaymentRecords.*;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/payments/refunds")
public class PaymentController {

    private final RefundService service;

    public PaymentController(RefundService service) {
        this.service = service;
    }

    @PostMapping
    public Refund initiate(@Valid @RequestBody RefundRequest request) {
        CallerHolder.requireInternal();
        return service.initiate(request);
    }

    @GetMapping("/{refundId}")
    public Refund get(@PathVariable String refundId) {
        return service.get(refundId);
    }
}
