-- ══════════════════════════════════════════════════════════
-- Database Schema: Two tables that will deadlock
--
-- The "broken app" updates orders then inventory in one
-- thread, and inventory then orders in another thread.
-- Classic lock ordering violation. This is the #1 cause
-- of deadlocks in monolith applications.
-- ══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS orders (
    order_id    SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    product_id  INTEGER NOT NULL,
    quantity    INTEGER NOT NULL DEFAULT 1,
    status      VARCHAR(20) NOT NULL DEFAULT 'pending',
    updated_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS inventory (
    product_id  INTEGER PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    stock       INTEGER NOT NULL DEFAULT 100,
    reserved    INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMP DEFAULT NOW()
);

-- Seed data: 10 products with stock
INSERT INTO inventory (product_id, name, stock) VALUES
    (1, 'Widget A', 500),
    (2, 'Widget B', 300),
    (3, 'Gadget X', 150),
    (4, 'Gadget Y', 200),
    (5, 'Part Alpha', 1000),
    (6, 'Part Beta', 750),
    (7, 'Assembly K', 50),
    (8, 'Assembly L', 80),
    (9, 'Module P', 400),
    (10, 'Module Q', 600);

-- Seed data: some existing orders
INSERT INTO orders (customer_id, product_id, quantity, status) VALUES
    (101, 1, 5, 'completed'),
    (102, 3, 2, 'completed'),
    (103, 5, 10, 'pending'),
    (104, 2, 1, 'pending'),
    (105, 7, 3, 'pending');

-- Agent tracking table (the agent logs its own activity here)
CREATE TABLE IF NOT EXISTS deadlock_events (
    event_id    SERIAL PRIMARY KEY,
    detected_at TIMESTAMP DEFAULT NOW(),
    session_a   INTEGER,
    session_b   INTEGER,
    table_a     VARCHAR(100),
    table_b     VARCHAR(100),
    diagnosis   TEXT,
    status      VARCHAR(20) DEFAULT 'detected',  -- detected, diagnosed, approved, fixed
    fixed_at    TIMESTAMP
);
