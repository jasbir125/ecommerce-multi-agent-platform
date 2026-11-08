package com.example.workflow.ai;

import com.example.common.web.PlatformRestClient;
import java.util.Map;
import org.springframework.ai.tool.annotation.Tool;
import org.springframework.ai.tool.annotation.ToolParam;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

/**
 * Read-only tools the model may call. They take no customer id from the model:
 * the calling customer comes from the authenticated request, so the model cannot
 * ask for another customer's data.
 */
@Component
public class EcommerceTools {

    private final RestClient orders;
    private final RestClient shipments;

    public EcommerceTools(PlatformRestClient clients, @Value("${services.order-url}") String orderUrl,
                          @Value("${SHIPMENT_SERVICE_URL:http://localhost:8082}") String shipmentUrl) {
        this.orders = clients.forBaseUrl(orderUrl);
        this.shipments = clients.forBaseUrl(shipmentUrl);
    }

    @Tool(description = "Get order status and amount for an order owned by the current customer")
    @SuppressWarnings("unchecked")
    public Map<String, Object> getOrderStatus(@ToolParam(description = "Order id, e.g. ORD-7842") String orderId) {
        return orders.get().uri("/api/orders/{id}", orderId).retrieve().body(Map.class);
    }

    @Tool(description = "Get shipment tracking status for an order owned by the current customer")
    @SuppressWarnings("unchecked")
    public Map<String, Object> getShipmentStatus(@ToolParam(description = "Order id, e.g. ORD-7842") String orderId) {
        return shipments.get().uri("/api/shipments/{id}", orderId).retrieve().body(Map.class);
    }
}
