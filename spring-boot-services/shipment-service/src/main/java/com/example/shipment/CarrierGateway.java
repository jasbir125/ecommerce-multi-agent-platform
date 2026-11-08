package com.example.shipment;

import com.example.common.web.ApiException;
import io.github.resilience4j.circuitbreaker.annotation.CircuitBreaker;
import java.time.LocalDate;
import java.util.Optional;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/**
 * Stand-in for the real carrier tracking API (it reads our shipment table).
 * It is wrapped in a circuit breaker so a failing carrier fails fast, and the
 * fallback raises 503 instead of returning made-up tracking data.
 */
@Component
public class CarrierGateway {

    private final JdbcClient jdbc;
    private final com.example.common.security.FaultInjector faults;

    public CarrierGateway(JdbcClient jdbc, com.example.common.security.FaultInjector faults) {
        this.jdbc = jdbc;
        this.faults = faults;
    }

    @CircuitBreaker(name = "carrier", fallbackMethod = "unavailable")
    public Optional<Row> track(String orderId) {
        faults.check("shipment", "carrier", orderId);      // test-only: the carrier API is down
        return jdbc.sql("""
                SELECT order_id, customer_id, carrier, tracking_no, status, expected_delivery, delay_days, last_event
                FROM shipment WHERE order_id = :id""").param("id", orderId).query(Row.class).optional();
    }

    @SuppressWarnings("unused")
    Optional<Row> unavailable(String orderId, Throwable t) {
        throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "SHIPMENT_UNAVAILABLE",
                "Carrier tracking is unavailable; shipment status could not be verified");
    }

    public record Row(String orderId, String customerId, String carrier, String trackingNo, String status,
                      LocalDate expectedDelivery, int delayDays, String lastEvent) {
    }
}
