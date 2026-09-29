-- ============================================================================
-- FleetPulse: TimescaleDB Initialization (Telemetry Hypertable)
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

CREATE TABLE IF NOT EXISTS telemetry (
    event_ts TIMESTAMPTZ NOT NULL,
    event_id UUID NOT NULL,
    vehicle_id UUID NOT NULL,
    seq BIGINT NOT NULL,
    oem_id VARCHAR(50) NOT NULL,
    vehicle_type VARCHAR(20) NOT NULL,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    speed_kmh REAL,
    odometer_km DOUBLE PRECISION,
    soc_pct REAL,
    voltage_v REAL,
    current_a REAL,
    temperature_c REAL,
    motor_temp_c REAL,
    engine_temp_c REAL,
    power_kw REAL,
    engine_load_pct REAL,
    rpm REAL,
    acceleration_ms2 REAL,
    harsh_brake BOOLEAN,
    event_type VARCHAR(30),
    dtc_codes TEXT[],
    ingested_at TIMESTAMPTZ NOT NULL
);

SELECT create_hypertable('telemetry', 'event_ts', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_telemetry_vehicle_ts ON telemetry (vehicle_id, event_ts DESC);

-- Idempotency safeguard for stream processor deduplication
CREATE UNIQUE INDEX IF NOT EXISTS ux_telemetry_vehicle_seq ON telemetry (vehicle_id, seq, event_ts);

-- Phase 4: hourly feature snapshots per vehicle component.
CREATE TABLE IF NOT EXISTS component_features (
    vehicle_id UUID NOT NULL,
    component VARCHAR(30) NOT NULL,
    feature_ts TIMESTAMPTZ NOT NULL,
    feature_schema_version VARCHAR(20) NOT NULL,
    window_complete BOOLEAN NOT NULL,
    features JSONB NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (vehicle_id, component, feature_ts)
);

SELECT create_hypertable('component_features', 'feature_ts', if_not_exists => TRUE);

-- Scratch table used by scripts/verify_feature_parity.py (one run per run_id).
CREATE TABLE IF NOT EXISTS component_features_parity (
    run_id VARCHAR(40) NOT NULL,
    vehicle_id UUID NOT NULL,
    component VARCHAR(30) NOT NULL,
    feature_ts TIMESTAMPTZ NOT NULL,
    feature_schema_version VARCHAR(20) NOT NULL,
    window_complete BOOLEAN NOT NULL,
    features JSONB NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, vehicle_id, component, feature_ts)
);
