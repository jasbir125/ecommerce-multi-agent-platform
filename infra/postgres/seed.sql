-- =============================================================================
-- Mock domain tables + demo data, plus outbox/inbox tables shared by services.
-- Runs after init.sql (see docker-compose mounts).
-- =============================================================================

-- Transactional outbox: services insert events in the same transaction as the
-- business change; a relay publishes them to Kafka (at-least-once).
CREATE TABLE outbox_event (
    id          BIGSERIAL PRIMARY KEY,
    event_id    VARCHAR(100) NOT NULL UNIQUE,
    topic       VARCHAR(200) NOT NULL,
    event_key   VARCHAR(200),
    payload     TEXT         NOT NULL,
    trace_context VARCHAR(100),                  -- W3C traceparent of the request that caused the event
    created_at  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    sent_at     TIMESTAMP
);
CREATE INDEX idx_outbox_unsent ON outbox_event (id) WHERE sent_at IS NULL;

-- Idempotent-consumer ledger: (consumer, event_id) is processed at most once.
CREATE TABLE processed_event (
    consumer     VARCHAR(100) NOT NULL,
    event_id     VARCHAR(100) NOT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (consumer, event_id)
);

CREATE TABLE customer_order (
    order_id       VARCHAR(100) PRIMARY KEY,
    customer_id    VARCHAR(100) NOT NULL,
    status         VARCHAR(50)  NOT NULL,        -- CONFIRMED/PROCESSING/SHIPPED/DELIVERED/CANCELLED
    payment_status VARCHAR(50)  NOT NULL,        -- CAPTURED/REFUNDED/...
    total_amount   NUMERIC(12,2) NOT NULL,
    currency       CHAR(3) DEFAULT 'USD',
    product_id     VARCHAR(100),
    product_name   VARCHAR(300),
    quantity       INT DEFAULT 1,
    replaces_order VARCHAR(100),                 -- set on replacement orders
    version        BIGINT NOT NULL DEFAULT 0,    -- optimistic locking
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_order_customer ON customer_order (customer_id);

CREATE TABLE order_replacement (
    replacement_order_id VARCHAR(100) PRIMARY KEY,
    original_order_id    VARCHAR(100) NOT NULL,
    workflow_id          VARCHAR(100) NOT NULL,
    reservation_id       VARCHAR(100) NOT NULL,
    idempotency_key      VARCHAR(150) NOT NULL UNIQUE,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE shipment (
    order_id          VARCHAR(100) PRIMARY KEY,
    customer_id       VARCHAR(100) NOT NULL,
    carrier           VARCHAR(100),
    tracking_no       VARCHAR(100),
    status            VARCHAR(50) NOT NULL,      -- IN_TRANSIT/DELAYED/LOST/DELIVERED
    expected_delivery DATE,
    delay_days        INT DEFAULT 0,
    last_event        VARCHAR(300)
);

CREATE TABLE carrier_case (
    case_id     VARCHAR(100) PRIMARY KEY,
    order_id    VARCHAR(100) NOT NULL UNIQUE,    -- one case per shipment exception
    workflow_id VARCHAR(100),
    reason      VARCHAR(300),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE product (
    product_id    VARCHAR(100) PRIMARY KEY,
    name          VARCHAR(300) NOT NULL,
    description   TEXT,
    category      VARCHAR(100),
    price         NUMERIC(12,2) NOT NULL,
    currency      CHAR(3) DEFAULT 'USD',
    available_qty INT NOT NULL CHECK (available_qty >= 0),   -- DB-level oversell guard
    reserved_qty  INT NOT NULL DEFAULT 0
);

-- ------------------------------------------------------------------ demo data
INSERT INTO product (product_id, name, description, category, price, available_qty) VALUES
 ('PRD-100','AeroSound Pro Wireless Headphones','Premium noise-cancelling over-ear headphones','headphones',129.99,0),
 ('PRD-101','AeroSound Lite Wireless Headphones','Noise-cancelling over-ear headphones, lighter build','headphones',99.99,25),
 ('PRD-102','BeatBox Studio Headphones','Studio-grade wired headphones','headphones',89.99,0),
 ('PRD-103','PulseFit Earbuds','True-wireless earbuds with charging case','headphones',79.99,40),
 ('PRD-200','TrailRunner Shoes','Lightweight running shoes','shoes',74.50,12);

INSERT INTO customer_order (order_id, customer_id, status, payment_status, total_amount, product_id, product_name) VALUES
 ('ORD-7842','CUST-1001','SHIPPED','CAPTURED',129.99,'PRD-100','AeroSound Pro Wireless Headphones'),
 ('ORD-1234','CUST-1001','CONFIRMED','CAPTURED',74.50,'PRD-200','TrailRunner Shoes'),
 ('ORD-9001','CUST-2002','SHIPPED','CAPTURED',59.00,'PRD-300','Desk Lamp'),
 -- extra lost shipments so the demo can be repeated (each order can be refunded once)
 ('ORD-8801','CUST-1001','SHIPPED','CAPTURED',129.99,'PRD-100','AeroSound Pro Wireless Headphones'),
 ('ORD-8802','CUST-1001','SHIPPED','CAPTURED',129.99,'PRD-100','AeroSound Pro Wireless Headphones'),
 ('ORD-8803','CUST-1001','SHIPPED','CAPTURED',129.99,'PRD-100','AeroSound Pro Wireless Headphones'),
 ('ORD-8804','CUST-1001','SHIPPED','CAPTURED',129.99,'PRD-100','AeroSound Pro Wireless Headphones');

INSERT INTO shipment (order_id, customer_id, carrier, tracking_no, status, expected_delivery, delay_days, last_event) VALUES
 ('ORD-7842','CUST-1001','BlueDart','BD-778421','DELAYED', CURRENT_DATE - 5, 5,'Delayed at regional hub; new estimate unknown'),
 ('ORD-9001','CUST-2002','BlueDart','BD-900112','IN_TRANSIT', CURRENT_DATE + 2, 0,'Out for delivery soon'),
 ('ORD-8801','CUST-1001','BlueDart','BD-880101','LOST', CURRENT_DATE - 5, 5,'No scan for 5 days; last seen at regional hub'),
 ('ORD-8802','CUST-1001','BlueDart','BD-880201','LOST', CURRENT_DATE - 5, 5,'No scan for 5 days; last seen at regional hub'),
 ('ORD-8803','CUST-1001','BlueDart','BD-880301','LOST', CURRENT_DATE - 6, 6,'No scan for 6 days'),
 ('ORD-8804','CUST-1001','BlueDart','BD-880401','LOST', CURRENT_DATE - 5, 5,'No scan for 5 days');
