-- Priority queue read model (CQRS): materialised from the maintenance_priority view, refreshed
-- by the scorer after each scoring pass. Sorted index serves keyset pagination directly.
CREATE MATERIALIZED VIEW IF NOT EXISTS maintenance_priority_mv AS
SELECT * FROM maintenance_priority;
CREATE UNIQUE INDEX IF NOT EXISTS ux_priority_mv_component ON maintenance_priority_mv (vehicle_component_id);
CREATE INDEX IF NOT EXISTS idx_priority_mv_tenant_loss
    ON maintenance_priority_mv (tenant_id, (round(coalesce(expected_loss, -1)::numeric, 2)::float) DESC, vehicle_component_id DESC);
CREATE INDEX IF NOT EXISTS idx_priority_mv_loss
    ON maintenance_priority_mv ((round(coalesce(expected_loss, -1)::numeric, 2)::float) DESC, vehicle_component_id DESC);

-- Admin audit pagination (Phase 6 query optimisation)
CREATE INDEX IF NOT EXISTS idx_audit_time_id ON audit_log (occurred_at DESC, audit_id DESC);
