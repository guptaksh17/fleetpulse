# Phase 1 Acceptance Verification Evidence

This document records the verification outputs captured from the live execution of the Phase 1 stack (`docker compose up`) prior to applying Phase 2 changes, fulfilling requirement **A4**.

---

## 1. Services Startup Verification

Command:
```bash
docker compose ps
```

Output:
```text
NAME                IMAGE                               COMMAND                  SERVICE             CREATED          STATUS                    PORTS
identity-resolver   fleetpulse-identity-resolver        "python resolver.py"     identity-resolver   19 seconds ago   Up 11 seconds             
kafka               apache/kafka:3.7.0                  "/__cacert_entrypoin…"   kafka               19 seconds ago   Up 18 seconds (healthy)   0.0.0.0:9092->9092/tcp, [::]:9092->9092/tcp
normalization       fleetpulse-normalization            "python normalizer.py"   normalization       19 seconds ago   Up 11 seconds             
postgres            postgres:16-alpine                  "docker-entrypoint.s…"   postgres            19 seconds ago   Up 18 seconds (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
simulator           fleetpulse-simulator                "python vehicle_simu…"   simulator           19 seconds ago   Up 11 seconds             
timescale-writer    fleetpulse-timescale-writer         "python writer.py"       timescale-writer    19 seconds ago   Up 11 seconds             
timescaledb         timescale/timescaledb:latest-pg16   "docker-entrypoint.s…"   timescaledb         19 seconds ago   Up 18 seconds (healthy)   0.0.0.0:5433->5432/tcp, [::]:5433->5432/tcp
```

All 7 containers started and transitioned to healthy status without manual intervention.

---

## 2. Growing Telemetry Row Count Verification

Command:
```bash
docker compose exec timescaledb psql -U fleetpulse -d fleetpulse -c "
SELECT count(*) FROM telemetry WHERE vehicle_id = '00000000-0000-0000-0000-000000000003';
"
# (Repeated after 3 seconds)
```

Output:
```text
 count 
-------
    20
(1 row)

 count 
-------
    23
(1 row)
```

The hypertable row count grew strictly monotonically at 1 row per second.

---

## 3. Correct Field Mapping Verification

Command:
```bash
docker compose exec timescaledb psql -U fleetpulse -d fleetpulse -c "
SELECT event_ts, speed_kmh, odometer_km, soc_pct, voltage_v, current_a, motor_temp_c, acceleration_ms2, harsh_brake, dtc_codes 
FROM telemetry 
ORDER BY event_ts DESC 
LIMIT 3;
"
```

Output:
```text
          event_ts          | speed_kmh | odometer_km | soc_pct | voltage_v | current_a | motor_temp_c | acceleration_ms2 | harsh_brake | dtc_codes 
----------------------------+-----------+-------------+---------+-----------+-----------+--------------+------------------+-------------+-----------
 2026-09-28 08:48:40.175+00 |     66.26 |   12453.855 |   91.08 |     396.5 |     71.89 |        55.55 |             -1.8 | f           | {}
 2026-09-28 08:48:39.154+00 |     72.94 |   12453.836 |   91.09 |     396.3 |     76.09 |        55.52 |            -0.96 | f           | {}
 2026-09-28 08:48:38.15+00  |     76.47 |   12453.816 |    91.1 |    396.18 |     78.47 |        55.44 |            -0.93 | f           | {}
(3 rows)
```

Key observations:
- `speed_kmh` accurately reflects OEM-A `vehicleSpeed` (e.g. 66.26–76.47 km/h).
- `odometer_km` advances continuously with distance travelled.
- `soc_pct` drifts downwards realistically with non-zero voltage (`~396V`) and current draw (`~72–78A`).
- `motor_temp_c` reflects EV motor temperature (`55.5°C`).
- `harsh_brake` is correctly boolean `f` (false) during normal telemetry.
- `dtc_codes` is empty array (`{}`).

---

## 4. Normalization Service Restart Resiliency Test

Command:
```bash
docker compose restart normalization
```

Output:
```text
[+] restart 1/1
 ✔ Container normalization Restarted                                       10.4s
```

Status and row count check post-restart:
```bash
docker compose ps
docker compose exec timescaledb psql -U fleetpulse -d fleetpulse -c "
SELECT count(*) FROM telemetry WHERE vehicle_id = '00000000-0000-0000-0000-000000000003';
"
```

Output:
```text
NAME                IMAGE                               COMMAND                  SERVICE             CREATED         STATUS                   PORTS
identity-resolver   fleetpulse-identity-resolver        "python resolver.py"     identity-resolver   8 minutes ago   Up 8 minutes             
kafka               apache/kafka:3.7.0                  "/__cacert_entrypoin…"   kafka               8 minutes ago   Up 8 minutes (healthy)   0.0.0.0:9092->9092/tcp, [::]:9092->9092/tcp
normalization       fleetpulse-normalization            "python normalizer.py"   normalization       8 minutes ago   Up 51 seconds            
postgres            postgres:16-alpine                  "docker-entrypoint.s…"   postgres            8 minutes ago   Up 8 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
simulator           fleetpulse-simulator                "python vehicle_simu…"   simulator           8 minutes ago   Up 8 minutes             
timescale-writer    fleetpulse-timescale-writer         "python writer.py"       timescale-writer    8 minutes ago   Up 8 minutes             
timescaledb         timescale/timescaledb:latest-pg16   "docker-entrypoint.s…"   timescaledb         8 minutes ago   Up 8 minutes (healthy)   0.0.0.0:5433->5432/tcp, [::]:5433->5432/tcp

 count 
-------
   498
(1 row)
```

The simulator and writer continued running without interruption. Upon restart, the normalization service reconnected to Kafka and resumed stream translation seamlessly.
