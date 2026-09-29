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
