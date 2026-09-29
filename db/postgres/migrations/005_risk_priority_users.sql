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
