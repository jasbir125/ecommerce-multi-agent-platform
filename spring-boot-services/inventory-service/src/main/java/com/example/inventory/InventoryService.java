package com.example.inventory;

import com.example.common.audit.AuditClient;
import com.example.common.events.EventPublisher;
import com.example.common.util.Ids;
import com.example.common.web.ApiException;
import com.example.inventory.InventoryRecords.*;
import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class InventoryService {

    private static final String PRODUCT_COLS = "product_id, name, description, category, price, currency, available_qty";

    private final JdbcClient jdbc;
    private final EventPublisher events;
    private final AuditClient audit;

    public InventoryService(JdbcClient jdbc, EventPublisher events, AuditClient audit) {
        this.jdbc = jdbc;
        this.events = events;
        this.audit = audit;
    }

    public List<Product> search(String q, String category, BigDecimal maxPrice, boolean inStockOnly) {
        return jdbc.sql("SELECT " + PRODUCT_COLS + """
                 FROM product
                WHERE (CAST(:q AS text) IS NULL OR name ILIKE '%' || :q || '%' OR description ILIKE '%' || :q || '%')
                  AND (CAST(:cat AS text) IS NULL OR category = :cat)
                  AND (CAST(:max AS numeric) IS NULL OR price <= :max)
                  AND (:stock = FALSE OR available_qty > 0)
                ORDER BY price""")
                .param("q", blankToNull(q)).param("cat", blankToNull(category))
                .param("max", maxPrice).param("stock", inStockOnly)
                .query(Product.class).list();
    }

    public Product get(String productId) {
        return jdbc.sql("SELECT " + PRODUCT_COLS + " FROM product WHERE product_id = :id")
                .param("id", productId).query(Product.class).optional()
                .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "PRODUCT_NOT_FOUND", "Unknown product"));
    }

    /**
     * Reserves stock atomically: the UPDATE only succeeds while enough stock is
     * left, so concurrent requests cannot oversell. Same idempotency key = same reservation.
     */
    @Transactional
    public Reservation reserve(ReservationRequest req) {
        Optional<Reservation> existing = findByKey(req.idempotencyKey());
        if (existing.isPresent()) {
            return existing.get();
        }
        int updated = jdbc.sql("""
                UPDATE product SET available_qty = available_qty - :q, reserved_qty = reserved_qty + :q
                WHERE product_id = :p AND available_qty >= :q""")
                .param("q", req.quantity()).param("p", req.productId()).update();
        if (updated == 0) {
            get(req.productId()); // 404 if the product does not exist at all
            throw new ApiException(HttpStatus.CONFLICT, "OUT_OF_STOCK", "Insufficient stock for " + req.productId());
        }
        String id = Ids.next("RSV");
        jdbc.sql("""
                INSERT INTO inventory_reservation (reservation_id, workflow_id, product_id, quantity, status, idempotency_key)
                VALUES (:id, :wf, :p, :q, 'RESERVED', :k)""")
                .param("id", id).param("wf", req.workflowId()).param("p", req.productId())
                .param("q", req.quantity()).param("k", req.idempotencyKey()).update();
        events.publish("inventory.reservation.completed", id, "InventoryReserved", req.workflowId(), null,
                Map.of("reservationId", id, "productId", req.productId(), "quantity", req.quantity()));
        audit.record(req.workflowId(), "inventory-service", "INVENTORY_RESERVED", null,
                Map.of("reservationId", id, "productId", req.productId()));
        return new Reservation(id, req.workflowId(), req.productId(), req.quantity(), "RESERVED", true);
    }

    public Reservation getReservation(String id) {
        return jdbc.sql("""
                SELECT reservation_id, workflow_id, product_id, quantity, status, FALSE AS created
                FROM inventory_reservation WHERE reservation_id = :id""")
                .param("id", id).query(Reservation.class).optional()
                .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "RESERVATION_NOT_FOUND", "Unknown reservation"));
    }

    /** Releasing is idempotent: only a RESERVED row is restored to stock. */
    @Transactional
    public Reservation release(String id) {
        Reservation r = getReservation(id);
        int flipped = jdbc.sql("""
                UPDATE inventory_reservation SET status = 'RELEASED', released_at = now()
                WHERE reservation_id = :id AND status = 'RESERVED'""").param("id", id).update();
        if (flipped == 1) {
            jdbc.sql("""
                    UPDATE product SET available_qty = available_qty + :q, reserved_qty = reserved_qty - :q
                    WHERE product_id = :p""").param("q", r.quantity()).param("p", r.productId()).update();
            events.publish("inventory.reservation.released", id, "InventoryReleased", r.workflowId(), null,
                    Map.of("reservationId", id, "productId", r.productId(), "quantity", r.quantity()));
            audit.record(r.workflowId(), "inventory-service", "INVENTORY_RELEASED", null, Map.of("reservationId", id));
        }
        return getReservation(id);
    }

    private Optional<Reservation> findByKey(String key) {
        return jdbc.sql("""
                SELECT reservation_id, workflow_id, product_id, quantity, status, FALSE AS created
                FROM inventory_reservation WHERE idempotency_key = :k""")
                .param("k", key).query(Reservation.class).optional();
    }

    private static String blankToNull(String s) {
        return s == null || s.isBlank() ? null : s;
    }
}
