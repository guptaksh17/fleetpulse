-- Phase 7: second OEM onboarded (nested payload v2.0 in US units; see contracts/oem/oem_b_v2.schema.json).
INSERT INTO oem (oem_id, name) VALUES ('OEM_B', 'Demo OEM B') ON CONFLICT DO NOTHING;
INSERT INTO oem_schema (oem_id, schema_version, schema_definition) VALUES ('OEM_B', '2.0', '{}'::jsonb) ON CONFLICT DO NOTHING;
