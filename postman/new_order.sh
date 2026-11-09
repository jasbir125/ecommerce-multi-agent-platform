#!/bin/sh
# Creates a fresh shipped-and-lost order for CUST-1001 so the end-to-end flow can be run again
# (the platform allows one refund per order). Prints the order id: paste it into the Postman variable `orderId`.
set -e
ID="ORD-PM$(date +%H%M%S)$(od -An -N1 -tu1 /dev/urandom | tr -d " ")"
docker exec -i platform-postgres psql -q -U "${POSTGRES_USER:-ecommerce}" -d "${POSTGRES_DB:-ecommerce}" <<SQL
INSERT INTO customer_order (order_id, customer_id, status, payment_status, total_amount, product_id, product_name)
VALUES ('$ID','CUST-1001','SHIPPED','CAPTURED',129.99,'PRD-100','AeroSound Pro Wireless Headphones');
INSERT INTO shipment (order_id, customer_id, carrier, tracking_no, status, expected_delivery, delay_days, last_event)
VALUES ('$ID','CUST-1001','BlueDart','BD-$ID','LOST', CURRENT_DATE - 5, 5, 'No scan for 5 days; last seen at regional hub');
UPDATE product SET available_qty = GREATEST(available_qty, 10) WHERE product_id IN ('PRD-101','PRD-103');
SQL
echo "$ID"
