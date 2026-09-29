"""
SQL for the API. Every query that returns fleet data is scoped by tenant through
vehicle -> fleet -> tenant (3NF, no denormalised tenant_id). %(tenant)s is NULL for admins.
Pagination is keyset (row-value comparison on the sort key), which stays O(log n) per page.
"""

TENANT = "(%(tenant)s::uuid IS NULL OR f.tenant_id = %(tenant)s::uuid)"

SUMMARY = f"""
SELECT
  (SELECT count(*) FROM vehicle v JOIN fleet f ON f.fleet_id = v.fleet_id WHERE {TENANT}) AS vehicles,
  (SELECT count(*) FROM maintenance_priority_mv f WHERE {TENANT}) AS components_scored,
  (SELECT count(*) FROM maintenance_priority_mv f WHERE {TENANT} AND f.p7d >= f.threshold) AS components_above_threshold,
  (SELECT round(coalesce(sum(expected_loss), 0)::numeric, 0) FROM maintenance_priority_mv f WHERE {TENANT}) AS total_expected_loss_7d,
  (SELECT count(*) FROM alert a JOIN vehicle_component vc ON vc.vehicle_component_id = a.vehicle_component_id
     JOIN vehicle v ON v.vehicle_id = vc.vehicle_id JOIN fleet f ON f.fleet_id = v.fleet_id
     WHERE a.status = 'ACTIVE' AND a.source = 'ML' AND {TENANT}) AS active_ml_alerts,
  (SELECT count(*) FROM alert a JOIN vehicle_component vc ON vc.vehicle_component_id = a.vehicle_component_id
     JOIN vehicle v ON v.vehicle_id = vc.vehicle_id JOIN fleet f ON f.fleet_id = v.fleet_id
     WHERE a.status = 'ACTIVE' AND a.source = 'RULE' AND {TENANT}) AS active_rule_alerts,
  (SELECT max(scored_at) FROM component_risk) AS last_scored_at
"""  # nosec B608 - only the constant TENANT fragment is interpolated; values are bound parameters

PRIORITY = f"""
SELECT f.vehicle_component_id::text, f.vehicle_id::text, f.vin, f.vehicle_type, f.component,
       round(f.p7d::numeric, 4)::float AS p7d, round(f.threshold::numeric, 4)::float AS threshold,
       round(coalesce(f.expected_loss, -1)::numeric, 2)::float AS expected_loss, f.cost_status, f.feature_ts, f.model_version
FROM maintenance_priority_mv f
WHERE {TENANT}
  AND (%(component)s::text IS NULL OR f.component = %(component)s::text)
  AND (%(c_loss)s::float IS NULL OR (round(coalesce(f.expected_loss, -1)::numeric, 2)::float, f.vehicle_component_id) < (%(c_loss)s::float, %(c_id)s::uuid))
ORDER BY round(coalesce(f.expected_loss, -1)::numeric, 2)::float DESC, f.vehicle_component_id DESC
LIMIT %(limit)s
"""  # nosec B608 - only the constant TENANT fragment is interpolated; values are bound parameters

ALERTS = f"""
SELECT a.alert_id::text, a.alert_type, a.severity, a.source, a.status, a.risk_probability, a.message,
       a.model_name, a.model_version, a.created_at, vc.component, v.vehicle_id::text, v.vin
FROM alert a
JOIN vehicle_component vc ON vc.vehicle_component_id = a.vehicle_component_id
JOIN vehicle v ON v.vehicle_id = vc.vehicle_id
JOIN fleet f ON f.fleet_id = v.fleet_id
WHERE a.status = %(status)s AND (%(source)s::text IS NULL OR a.source = %(source)s::text) AND {TENANT}
  AND (%(c_ts)s::timestamptz IS NULL OR (a.created_at, a.alert_id) < (%(c_ts)s::timestamptz, %(c_id)s::uuid))
ORDER BY a.created_at DESC, a.alert_id DESC
LIMIT %(limit)s
"""  # nosec B608 - only the constant TENANT fragment is interpolated; values are bound parameters

ACK_ALERT = f"""
UPDATE alert a SET status = 'ACKNOWLEDGED'
FROM vehicle_component vc, vehicle v, fleet f
WHERE a.alert_id = %(alert_id)s::uuid AND a.status = 'ACTIVE'
  AND vc.vehicle_component_id = a.vehicle_component_id AND v.vehicle_id = vc.vehicle_id AND f.fleet_id = v.fleet_id AND {TENANT}
RETURNING a.alert_id::text, a.status
"""  # nosec B608 - only the constant TENANT fragment is interpolated; values are bound parameters

VEHICLES = f"""
SELECT v.vehicle_id::text, v.vin, v.vehicle_type, v.make, v.model, v.year, round(v.current_odometer_km::numeric, 1)::float AS odometer_km,
       f.name AS fleet,
       (SELECT round(max(r.p7d)::numeric, 4)::float FROM vehicle_component vc JOIN component_risk r ON r.vehicle_component_id = vc.vehicle_component_id
         WHERE vc.vehicle_id = v.vehicle_id) AS max_p7d
FROM vehicle v JOIN fleet f ON f.fleet_id = v.fleet_id
WHERE {TENANT} AND (%(q)s::text IS NULL OR v.vin LIKE %(q)s::text)
  AND (%(c_id)s::uuid IS NULL OR v.vehicle_id > %(c_id)s::uuid)
ORDER BY v.vehicle_id
LIMIT %(limit)s
"""  # nosec B608 - only the constant TENANT fragment is interpolated; values are bound parameters

VEHICLE = f"""
SELECT v.vehicle_id::text, v.vin, v.vehicle_type, v.make, v.model, v.year, v.manufacture_date,
       round(v.current_odometer_km::numeric, 1)::float AS odometer_km, f.name AS fleet, f.tenant_id::text
FROM vehicle v JOIN fleet f ON f.fleet_id = v.fleet_id
WHERE v.vehicle_id = %(vehicle_id)s::uuid AND {TENANT}
"""  # nosec B608 - only the constant TENANT fragment is interpolated; values are bound parameters

VEHICLE_COMPONENTS = """
SELECT vc.vehicle_component_id::text, vc.component, vc.status,
       round(r.p7d::numeric, 4)::float AS p7d, round(r.threshold::numeric, 4)::float AS threshold, r.feature_ts, r.model_version,
       round(p.expected_loss::numeric, 2)::float AS expected_loss, p.cost_status
FROM vehicle_component vc
LEFT JOIN component_risk r ON r.vehicle_component_id = vc.vehicle_component_id
LEFT JOIN maintenance_priority p ON p.vehicle_component_id = vc.vehicle_component_id
WHERE vc.vehicle_id = %(vehicle_id)s::uuid
ORDER BY vc.component
"""

VEHICLE_EVENTS = """
SELECT me.event_type, me.occurred_at, round(me.odometer_km::numeric, 1)::float AS odometer_km, vc.component
FROM maintenance_event me JOIN vehicle_component vc ON vc.vehicle_component_id = me.vehicle_component_id
WHERE vc.vehicle_id = %(vehicle_id)s::uuid
ORDER BY me.occurred_at DESC
LIMIT 50
"""

TELEMETRY = """
SELECT event_ts, speed_kmh, round(odometer_km::numeric, 2)::float AS odometer_km, soc_pct, voltage_v, current_a,
       temperature_c AS battery_temp_c, motor_temp_c, engine_temp_c, power_kw, rpm, latitude, longitude, dtc_codes, harsh_brake
FROM telemetry
WHERE vehicle_id = %(vehicle_id)s::uuid
ORDER BY event_ts DESC
LIMIT %(limit)s
"""

AUDIT = """
SELECT audit_id::text, occurred_at, actor_type, actor_id, action, entity_type, entity_id::text, metadata
FROM audit_log
WHERE (%(c_ts)s::timestamptz IS NULL OR (occurred_at, audit_id) < (%(c_ts)s::timestamptz, %(c_id)s::uuid))
ORDER BY occurred_at DESC, audit_id DESC
LIMIT %(limit)s
"""

VEHICLE_BY_VIN = f"""
SELECT v.vehicle_id::text
FROM vehicle v JOIN fleet f ON f.fleet_id = v.fleet_id
WHERE v.vin = %(vin)s AND {TENANT}
"""  # nosec B608 - only the constant TENANT fragment is interpolated; values are bound parameters

RISK_BREAKDOWN = f"""
SELECT f.component,
       count(*) AS scored,
       count(*) FILTER (WHERE f.p7d >= f.threshold) AS above_threshold,
       round(coalesce(sum(f.expected_loss), 0)::numeric, 0)::float AS expected_loss,
       round(avg(f.p7d)::numeric, 4)::float AS mean_p7d,
       count(*) FILTER (WHERE f.p7d < 0.05) AS b_0_5,
       count(*) FILTER (WHERE f.p7d >= 0.05 AND f.p7d < 0.10) AS b_5_10,
       count(*) FILTER (WHERE f.p7d >= 0.10 AND f.p7d < 0.25) AS b_10_25,
       count(*) FILTER (WHERE f.p7d >= 0.25 AND f.p7d < 0.50) AS b_25_50,
       count(*) FILTER (WHERE f.p7d >= 0.50) AS b_50_100
FROM maintenance_priority_mv f
WHERE {TENANT}
GROUP BY f.component
ORDER BY f.component
"""  # nosec B608 - only the constant TENANT fragment is interpolated; values are bound parameters

FLEET_COMPOSITION = f"""
SELECT v.vehicle_type, v.oem_id, count(*) AS vehicles
FROM vehicle v JOIN fleet f ON f.fleet_id = v.fleet_id
WHERE {TENANT}
GROUP BY 1, 2
ORDER BY 1, 2
"""  # nosec B608 - only the constant TENANT fragment is interpolated; values are bound parameters

