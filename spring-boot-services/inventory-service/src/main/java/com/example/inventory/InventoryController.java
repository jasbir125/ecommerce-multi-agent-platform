package com.example.inventory;

import com.example.common.security.CallerHolder;
import com.example.inventory.InventoryRecords.*;
import jakarta.validation.Valid;
import java.math.BigDecimal;
import java.util.List;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api")
public class InventoryController {

    private final InventoryService service;

    public InventoryController(InventoryService service) {
        this.service = service;
    }

    @GetMapping("/catalog/products/search")
    public List<Product> search(@RequestParam(required = false) String q,
                                @RequestParam(required = false) String category,
                                @RequestParam(required = false) BigDecimal maxPrice,
                                @RequestParam(defaultValue = "true") boolean inStockOnly) {
        return service.search(q, category, maxPrice, inStockOnly);
    }

    @GetMapping("/inventory/{productId}")
    public Product get(@PathVariable String productId) {
        return service.get(productId);
    }

    @PostMapping("/inventory/reservations")
    public Reservation reserve(@Valid @RequestBody ReservationRequest request) {
        CallerHolder.requireInternal();
        return service.reserve(request);
    }

    @GetMapping("/inventory/reservations/{reservationId}")
    public Reservation getReservation(@PathVariable String reservationId) {
        CallerHolder.requireInternal();
        return service.getReservation(reservationId);
    }

    @PostMapping("/inventory/reservations/{reservationId}/release")
    public Reservation release(@PathVariable String reservationId) {
        CallerHolder.requireInternal();
        return service.release(reservationId);
    }
}
