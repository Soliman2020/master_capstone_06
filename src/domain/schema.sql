-- P6 property-management SQLite schema.
-- Small, deterministic seed data is inserted by tools.py at first run.

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id  TEXT PRIMARY KEY,       -- T-0042
    name       TEXT NOT NULL,
    email      TEXT,
    phone      TEXT,
    unit_id    TEXT
);

CREATE TABLE IF NOT EXISTS units (
    unit_id    TEXT PRIMARY KEY,       -- 4B
    building   TEXT,
    rent_usd   REAL
);

CREATE TABLE IF NOT EXISTS leases (
    lease_id   TEXT PRIMARY KEY,
    unit_id    TEXT NOT NULL,
    tenant_id  TEXT NOT NULL,
    start_date TEXT,
    end_date   TEXT,
    status     TEXT                    -- active / expired / pending_renewal
);

CREATE TABLE IF NOT EXISTS maintenance (
    maint_id   TEXT PRIMARY KEY,
    unit_id    TEXT NOT NULL,
    vendor     TEXT,
    cost_estimate REAL,
    status     TEXT                    -- scheduled / done
);

CREATE TABLE IF NOT EXISTS inbox (
    msg_id     TEXT PRIMARY KEY,
    tenant_id  TEXT,
    body       TEXT,
    ts         TEXT
);