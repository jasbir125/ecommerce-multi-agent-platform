package com.example.order;

import com.example.common.security.CallerHolder;
import com.example.order.OrderRecords.*;
import jakarta.validation.Valid;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api")
public class OrderController {

    private final OrderService service;

    public OrderController(OrderService service) {
        this.service = service;
    }

    @GetMapping("/orders/{orderId}")
    public Order get(@PathVariable String orderId) {
        return service.getOwned(orderId);
    }

    @GetMapping("/customers/{customerId}/orders")
    public List<Order> list(@PathVariable String customerId) {
        return service.listForCustomer(customerId);
    }

    @GetMapping("/orders/{orderId}/owner")
    public OwnerResponse owner(@PathVariable String orderId) {
        return service.owner(orderId);
    }

    @PostMapping("/orders/{orderId}/cancel")
    public CancelResponse cancel(@PathVariable String orderId) {
        return service.cancel(orderId);
    }

    @PostMapping("/orders/replacement")
    @ResponseStatus(HttpStatus.CREATED)
    public ReplacementResponse replacement(@Valid @RequestBody ReplacementRequest request) {
        return service.createReplacement(request);
    }
}
