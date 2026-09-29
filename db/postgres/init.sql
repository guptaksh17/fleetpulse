-- ============================================================================
-- FleetPulse: PostgreSQL Database Initialization (Phase 1 + Phase 2)
-- ============================================================================

CREATE TABLE tenant (
    tenant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(150) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE fleet (
    fleet_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenant(tenant_id),
    name VARCHAR(150) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE oem (
    oem_id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','INACTIVE')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE oem_schema (
    oem_id VARCHAR(50) NOT NULL REFERENCES oem(oem_id),
    schema_version VARCHAR(30) NOT NULL,
    schema_definition JSONB NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','DEPRECATED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (oem_id, schema_version)
);

CREATE TABLE vehicle (
    vehicle_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fleet_id UUID NOT NULL REFERENCES fleet(fleet_id),
    vin VARCHAR(17) NOT NULL UNIQUE,
    oem_id VARCHAR(50) NOT NULL REFERENCES oem(oem_id),
    vehicle_type VARCHAR(20) NOT NULL CHECK (vehicle_type IN ('ICE','EV','HYBRID')),
    make VARCHAR(100),
    model VARCHAR(100),
    year INT,
    manufacture_date DATE,
    current_odometer_km DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- Phase 2 Tables: vehicle_component, alert, audit_log
-- ============================================================================

CREATE TABLE vehicle_component (
    vehicle_component_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vehicle_id UUID NOT NULL REFERENCES vehicle(vehicle_id),
    component VARCHAR(30) NOT NULL CHECK (component IN ('BRAKE','POWERTRAIN','BATTERY')),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','NOT_APPLICABLE')),
    installed_at TIMESTAMPTZ,
    UNIQUE (vehicle_id, component)
);

CREATE TABLE alert (
    alert_id UUID PRIMARY KEY,
    vehicle_component_id UUID NOT NULL REFERENCES vehicle_component(vehicle_component_id),
    alert_type VARCHAR(40) NOT NULL,
    severity VARCHAR(20) NOT NULL CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    source VARCHAR(20) NOT NULL CHECK (source IN ('ML','RULE')),
    risk_probability DOUBLE PRECISION,
    message TEXT NOT NULL,
    model_name VARCHAR(100),
    model_version VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','ACKNOWLEDGED','RESOLVED'))
);

CREATE UNIQUE INDEX idx_alert_active_dedup ON alert (vehicle_component_id, alert_type) WHERE status = 'ACTIVE';
CREATE INDEX idx_alert_component_created ON alert (vehicle_component_id, created_at DESC);

CREATE TABLE audit_log (
    audit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenant(tenant_id),
    actor_type VARCHAR(20) NOT NULL CHECK (actor_type IN ('USER','SYSTEM','SERVICE')),
    actor_id VARCHAR(100),
    action VARCHAR(100) NOT NULL,
    entity_type VARCHAR(50),
    entity_id UUID,
    metadata JSONB,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id VARCHAR(100)
);

CREATE INDEX idx_audit_tenant_time ON audit_log (tenant_id, occurred_at DESC);

-- ============================================================================
-- Phase 3 Tables: driver, trip, maintenance_event
-- ============================================================================

CREATE TABLE driver (
    driver_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenant(tenant_id),
    name VARCHAR(150),
    license_number VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE trip (
    trip_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vehicle_id UUID NOT NULL REFERENCES vehicle(vehicle_id),
    driver_id UUID REFERENCES driver(driver_id),
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    distance_km DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_trip_vehicle_started ON trip (vehicle_id, started_at);

CREATE TABLE maintenance_event (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vehicle_component_id UUID NOT NULL REFERENCES vehicle_component(vehicle_component_id),
    event_type VARCHAR(30) NOT NULL CHECK (event_type IN ('MAINTENANCE_REQUIRED', 'FAILURE', 'SERVICE_COMPLETED')),
    occurred_at TIMESTAMPTZ NOT NULL,
    odometer_km DOUBLE PRECISION,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_maintenance_component_time ON maintenance_event (vehicle_component_id, occurred_at);

-- ============================================================================
-- Seed data for Phase 1 & Phase 2
-- ============================================================================

INSERT INTO tenant (tenant_id, name) 
VALUES ('00000000-0000-0000-0000-000000000001', 'Demo Tenant');

INSERT INTO fleet (fleet_id, tenant_id, name) 
VALUES ('00000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', 'Demo Fleet');

INSERT INTO oem (oem_id, name) 
VALUES ('OEM_A', 'Demo OEM A');

INSERT INTO oem_schema (oem_id, schema_version, schema_definition) 
VALUES ('OEM_A', '1.0', '{}'::jsonb);

INSERT INTO vehicle (vehicle_id, fleet_id, vin, oem_id, vehicle_type)
VALUES ('00000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000002', '1HGCM82633A004352', 'OEM_A', 'EV');

-- Seed vehicle_component for demo vehicle (EV, so all three ACTIVE):
INSERT INTO vehicle_component (vehicle_component_id, vehicle_id, component, status, installed_at)
VALUES 
('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-000000000003', 'BRAKE', 'ACTIVE', now()),
('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-000000000003', 'POWERTRAIN', 'ACTIVE', now()),
('00000000-0000-0000-0000-0000000000a3', '00000000-0000-0000-0000-000000000003', 'BATTERY', 'ACTIVE', now());
-- Phase 5/6: calibrated 7-day risk per component, cost profiles, priority view, API users.

CREATE TABLE IF NOT EXISTS component_risk (
    vehicle_component_id UUID PRIMARY KEY REFERENCES vehicle_component(vehicle_component_id),
    model_version VARCHAR(40) NOT NULL,
    model_kind VARCHAR(20) NOT NULL,
    feature_ts TIMESTAMPTZ NOT NULL,
    p7d DOUBLE PRECISION NOT NULL CHECK (p7d >= 0 AND p7d <= 1),
    threshold DOUBLE PRECISION NOT NULL,
    scored_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_component_risk_p7d ON component_risk (p7d DESC);

-- Cost inputs per component and vehicle type. A missing row yields COST_DATA_REQUIRED, never zero.
CREATE TABLE IF NOT EXISTS component_cost (
    component VARCHAR(30) NOT NULL CHECK (component IN ('BRAKE','POWERTRAIN','BATTERY')),
    vehicle_type VARCHAR(20) NOT NULL CHECK (vehicle_type IN ('ICE','EV','HYBRID')),
    direct_failure_cost DOUBLE PRECISION NOT NULL,
    expected_downtime_hours DOUBLE PRECISION NOT NULL,
    downtime_cost_per_hour DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (component, vehicle_type)
);

-- Priority: expected loss = P7d * (direct failure cost + downtime hours * cost per hour).
CREATE OR REPLACE VIEW maintenance_priority AS
SELECT vc.vehicle_component_id, vc.vehicle_id, vc.component, v.vin, v.vehicle_type, v.fleet_id, f.tenant_id,
       r.p7d, r.threshold, r.feature_ts, r.model_version,
       CASE WHEN c.component IS NULL THEN NULL
            ELSE r.p7d * (c.direct_failure_cost + c.expected_downtime_hours * c.downtime_cost_per_hour) END AS expected_loss,
       CASE WHEN c.component IS NULL THEN 'COST_DATA_REQUIRED' ELSE 'OK' END AS cost_status
FROM component_risk r
JOIN vehicle_component vc ON vc.vehicle_component_id = r.vehicle_component_id
JOIN vehicle v ON v.vehicle_id = vc.vehicle_id
JOIN fleet f ON f.fleet_id = v.fleet_id
LEFT JOIN component_cost c ON c.component = vc.component AND c.vehicle_type = v.vehicle_type;

CREATE TABLE IF NOT EXISTS app_user (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(200) NOT NULL UNIQUE,
    password_hash VARCHAR(300) NOT NULL,
    role VARCHAR(30) NOT NULL CHECK (role IN ('ADMIN','FLEET_MANAGER','VIEWER')),
    tenant_id UUID REFERENCES tenant(tenant_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Keyset pagination and tenant scoping helpers.
CREATE INDEX IF NOT EXISTS idx_vehicle_fleet ON vehicle (fleet_id, vehicle_id);
CREATE INDEX IF NOT EXISTS idx_alert_status_created ON alert (status, created_at DESC, alert_id);
CREATE INDEX IF NOT EXISTS idx_vehicle_component_vehicle ON vehicle_component (vehicle_id);

INSERT INTO component_cost VALUES
 ('BRAKE','ICE',1800,16,95), ('BRAKE','EV',2000,16,95), ('BRAKE','HYBRID',1900,16,95),
 ('POWERTRAIN','ICE',6500,48,95), ('POWERTRAIN','EV',8000,40,95), ('POWERTRAIN','HYBRID',7200,44,95),
 ('BATTERY','EV',12000,72,95), ('BATTERY','HYBRID',7000,48,95)
ON CONFLICT DO NOTHING;
-- Priority queue read model (CQRS): materialised from the maintenance_priority view, refreshed
-- by the scorer after each scoring pass. Sorted index serves keyset pagination directly.
CREATE MATERIALIZED VIEW IF NOT EXISTS maintenance_priority_mv AS
SELECT * FROM maintenance_priority;
CREATE UNIQUE INDEX IF NOT EXISTS ux_priority_mv_component ON maintenance_priority_mv (vehicle_component_id);
CREATE INDEX IF NOT EXISTS idx_priority_mv_tenant_loss
    ON maintenance_priority_mv (tenant_id, (round(coalesce(expected_loss, -1)::numeric, 2)::float) DESC, vehicle_component_id DESC);
CREATE INDEX IF NOT EXISTS idx_priority_mv_loss
    ON maintenance_priority_mv ((round(coalesce(expected_loss, -1)::numeric, 2)::float) DESC, vehicle_component_id DESC);

CREATE INDEX IF NOT EXISTS idx_audit_time_id ON audit_log (occurred_at DESC, audit_id DESC);
-- Phase 7: second OEM onboarded (nested payload v2.0 in US units; see contracts/oem/oem_b_v2.schema.json).
INSERT INTO oem (oem_id, name) VALUES ('OEM_B', 'Demo OEM B') ON CONFLICT DO NOTHING;
INSERT INTO oem_schema (oem_id, schema_version, schema_definition) VALUES ('OEM_B', '2.0', '{}'::jsonb) ON CONFLICT DO NOTHING;
