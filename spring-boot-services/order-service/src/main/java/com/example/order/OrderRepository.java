package com.example.order;

import com.example.order.OrderRecords.Order;
import java.util.List;
import java.util.Optional;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

@Repository
public class OrderRepository {

    private static final String COLS = """
            order_id, customer_id, status, payment_status, total_amount, currency,
            product_id, product_name, quantity, replaces_order, version""";

    private final JdbcClient jdbc;

    public OrderRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public Optional<Order> find(String orderId) {
        return jdbc.sql("SELECT " + COLS + " FROM customer_order WHERE order_id = :id")
                .param("id", orderId).query(Order.class).optional();
    }

    public List<Order> findByCustomer(String customerId) {
        return jdbc.sql("SELECT " + COLS + " FROM customer_order WHERE customer_id = :c ORDER BY created_at DESC")
                .param("c", customerId).query(Order.class).list();
    }

    /** Optimistic update: succeeds only if the row is still at the version we read. */
    public boolean cancel(String orderId, long expectedVersion) {
        return jdbc.sql("""
                UPDATE customer_order SET status = 'CANCELLED', version = version + 1, updated_at = now()
                WHERE order_id = :id AND version = :v AND status IN ('CONFIRMED','PROCESSING')""")
                .param("id", orderId).param("v", expectedVersion).update() == 1;
    }
}
