package com.example.shipment;

import com.example.common.security.CallerHolder;
import com.example.shipment.ShipmentService.*;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/shipments")
public class ShipmentController {

    private final ShipmentService service;

    public ShipmentController(ShipmentService service) {
        this.service = service;
    }

    @GetMapping("/{orderId}")
    public Shipment get(@PathVariable String orderId) {
        return service.get(orderId);
    }

    @PostMapping("/{orderId}/carrier-case")
    public CarrierCase createCase(@PathVariable String orderId, @RequestBody(required = false) CarrierCaseRequest body) {
        return service.createCase(orderId, body);
    }
}
