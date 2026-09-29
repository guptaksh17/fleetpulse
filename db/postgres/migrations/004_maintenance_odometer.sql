-- Phase 4 Part A4: service records carry the odometer reading at the time of the event.
ALTER TABLE maintenance_event ADD COLUMN IF NOT EXISTS odometer_km DOUBLE PRECISION;
