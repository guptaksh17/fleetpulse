# FleetPulse Feature Specification (feature_schema_version f1)

Source of truth in code:
- `configs/features.yaml` (windows, buckets, thresholds)
- `fleetpulse_features/stats.py` (bucket statistics, one definition for streaming and offline)
- `fleetpulse_features/features.py` (`features_from_window_stats`, the only place formulas live)
- `fleetpulse_features/labels.py` and `fleetpulse_features/dataset.py` (labels, exclusions, splits)

## 1. Snapshots and windows

- One snapshot per vehicle per ACTIVE component at every top of the hour of simulated time
  (`snapshot_interval_seconds = 3600`).
- A vehicle gets snapshots at every hour boundary t with first_event_ts < t <= last_event_ts.
- Windows are half-open, [t - W, t). Only telemetry with event_ts < t is used. An event exactly at t
  belongs to the next snapshot.

| Window | Length | Bucket width | Buckets merged | Retention |
|---|---|---|---|---|
| 5m | 300 s | 60 s | 5 | 2 h (for the 60 s buckets) |
| 1h | 3600 s | 60 s | 60 | 2 h |
| 24h | 86400 s | 900 s | 96 | 25 h |

Retention is the longest window using that bucket width plus 1 h.

### 24h bucket width deviation

The earlier plan used 1-minute buckets for every window. The 24h window uses 900 s buckets instead,
for two reasons:
- **Memory.** A 24h window of 60 s buckets needs 1,440 buckets per vehicle (1,500 with retention),
  each holding about 70 statistics. 900 s buckets need 100 buckets per vehicle.
- **Event cadence.** Parked vehicles report every 900 s. Most of a 24h window is parked time, so finer
  buckets add memory without adding resolution.

Snapshot-time equality with exact windows still holds. The load-time config check enforces that every
bucket width divides both its window and the 3600 s snapshot interval. A snapshot time t is therefore
a multiple of every bucket width, t - W is also a bucket boundary, and [t - W, t) is exactly a whole
number of buckets. Merging those buckets gives the same result as aggregating the raw events in
[t - W, t). The equality holds only at snapshot times: a snapshot at an arbitrary instant would need
partial buckets, which this design does not support. The parity test
(`scripts/verify_feature_parity.py`) checks this equality against the event-level offline reference.

## 2. Bucket statistics

Each statistic has one merge operation:
- count and sum: added
- min and max
- first and last: the value with the smallest or largest (event_ts, seq)

Null inputs contribute nothing; they are never zero-filled.

Signal values are rounded to float32 before use, because the telemetry table stores them as REAL.
Live Kafka events, rows replayed from TimescaleDB and the offline reference therefore see
bit-identical inputs.

| Statistic | Op | Definition |
|---|---|---|
| n_events | count | every event |
| n_moving | count | speed_kmh > 1 |
| speed_sum, speed_sumsq | sum | speed and speed squared over moving events |
| odo_min, odo_max | min, max | odometer_km |
| harsh_count | count | harsh_brake is true |
| harsh_high_speed_count | count | harsh_brake and speed_kmh >= 80 at the reported sample |
| harsh_accel_n, harsh_accel_sum | count, sum | acceleration on harsh-brake events |
| accel_min | min | acceleration_ms2 |
| brake_n, brake_accel_sum | count, sum | events with acceleration < -1.0 m/s2 |
| dtc_event_count | count | events with at least one DTC |
| dtc_brake_count, dtc_powertrain_count, dtc_battery_count | count | events with at least one DTC of that component (from `contracts/dtc/dtc_registry.yaml`) |
| {s}_n, {s}_sum, {s}_sumsq, {s}_max, {s}_min | count, sum, sum, max, min | for s in battery_temp, engine_temp, motor_temp, voltage, current, power, engine_load, rpm |
| engine_temp_high_count | count | engine_temp_c > 105 |
| engine_load_high_count | count | engine_load_pct > 80 |
| motor_temp_high_count | count | motor_temp_c > 90 |
| power_high_count | count | power_kw > 60 |
| battery_temp_high_count | count | battery temperature > 40 |
| current_absmax | max | abs(current_a) |
| soc_min, soc_max | min, max | soc_pct |
| soc_first, soc_last | first, last | soc_pct |
| deep_discharge_count | count | soc_pct < 10 |
| charging_count | count | current_a < -1 A |
| fast_charge_count | count | current_a < -100 A |

### Threshold notes

- **Power.** The simulator has no rated power. It applies a fixed 60 kW stress threshold, which it
  describes as 70 percent of peak, and the feature threshold mirrors that constant.
- **Charging.** The telemetry table has no charging flag, so charging is derived from the sign of the
  pack current.
- **Fast charging.** The simulator only models 11 kW AC charging, which draws about -28 A.
  fast_charge_events is therefore always 0 in the current data. It is kept so the schema is ready for
  DC fast-charge simulation.

## 3. Feature formulas

Each window w in {5m, 1h, 24h} produces the features below with the prefix `w{w}_`, for example
`w1h_engine_temp_mean`.

The rules for undefined values:
- Counts are 0 when the window is empty.
- Means, standard deviations, minima and maxima are NaN when there is no contributing event.
- A signal the vehicle type does not have is NaN for every window: engine temperature, load and rpm
  for EVs; motor temperature and power for ICE vehicles.

Standard deviation is the population standard deviation, computed as sqrt(max(0, sumsq/n - mean^2))
in float64.

### Common (every component)

| Feature | Formula | Unit |
|---|---|---|
| events | n_events | count |
| moving_events | n_moving | count |
| avg_speed_moving | speed_sum / n_moving | km/h |
| speed_std_moving | std from speed_sum, speed_sumsq, n_moving | km/h |
| distance_km | odo_max - odo_min | km |
| dtc_events | dtc_event_count | count |
| component_dtc_events | dtc_{component}_count | count |

### BRAKE

| Feature | Formula | Unit |
|---|---|---|
| harsh_brakes | harsh_count | count |
| harsh_high_speed | harsh_high_speed_count | count |
| min_accel | accel_min | m/s2 |
| mean_harsh_decel | -harsh_accel_sum / harsh_accel_n | m/s2 (positive) |
| mean_braking_decel | -brake_accel_sum / brake_n | m/s2 (positive) |

### POWERTRAIN

Engine features apply to ICE and HYBRID. Motor and power features apply to EV and HYBRID.

| Feature | Formula | Unit |
|---|---|---|
| engine_temp_mean, engine_temp_max, engine_temp_std | from engine_temp stats | C |
| high_engine_temp_events | engine_temp_high_count | count |
| engine_load_mean | engine_load_sum / engine_load_n | percent |
| high_load_events | engine_load_high_count | count |
| rpm_mean, rpm_std | from rpm stats | rpm |
| motor_temp_mean, motor_temp_max, motor_temp_std | from motor_temp stats | C |
| high_motor_temp_events | motor_temp_high_count | count |
| power_mean, power_max | from power stats | kW |
| high_power_events | power_high_count | count |

### BATTERY

| Feature | Formula | Unit |
|---|---|---|
| soc_min, soc_max | soc_min, soc_max | percent |
| soc_delta | soc_last - soc_first | percent |
| battery_temp_mean, battery_temp_max, battery_temp_std | from battery_temp stats | C |
| high_temp_events | battery_temp_high_count | count |
| voltage_mean, voltage_std, voltage_min | from voltage stats | V |
| current_mean, current_std | from current stats | A |
| current_max_abs | current_absmax | A |
| charging_events | charging_count (NaN if no current reported) | count |
| fast_charge_events | fast_charge_count (NaN if no current reported) | count |
| deep_discharge_events | deep_discharge_count (NaN if no SoC reported) | count |

### Static context (every component, no window prefix)

| Feature | Formula | Unit |
|---|---|---|
| days_since_service | (t - occurred_at of the last SERVICE_COMPLETED of this component with occurred_at <= t) / 1 day; NaN if none | days |
| has_service_history | 1 if such a service exists, else 0 | flag |
| distance_since_service_km | odometer_km - odometer_km recorded on that service; NaN if none | km |
| vehicle_age_days | (t - vehicle.manufacture_date) / 1 day | days |
| odometer_km | w24h odo_max (latest odometer before t); NaN if no event in 24 h | km |
| vt_ice, vt_ev, vt_hybrid | one-hot vehicle type | flag |

`window_complete` is stored beside the features. It is true only when first_event_ts <= t - 24 h, and
the dataset excludes rows where it is false.

The feature counts per component are:
- BRAKE: 44
- POWERTRAIN: 74
- BATTERY: 77

## 4. Inputs that are never used

- `driving_profile` and `driver_behavior`.
- Anything from the simulator's hidden state: health, wear, lifecycle, stress and service delay.
- `maintenance_event.metadata`, which holds hidden health and wear for offline analysis only.

The static context reads only vehicle_type, manufacture_date, the ACTIVE components, and the
occurred_at and odometer_km of SERVICE_COMPLETED rows. `tests/test_hidden_state.py` enforces this.

## 5. Numerical limitation of std

Standard deviation from running sums (sumsq/n - mean^2) loses precision when the mean is large
relative to the spread. For example, pack voltage runs near 390 V with a spread of about 0.2 V, so the
two terms are about 1.5e5 and their difference is about 0.04. float64 keeps about 16 significant
digits, so the absolute error in the variance is around 1e-11, which is negligible here. Small
negative variances caused by rounding are clamped to 0.

A shifted-data or Welford formulation would be exact but is not mergeable with the simple add
operations used in Redis. It is not needed at the current signal scales.

## 6. Streaming state in Redis and memory

The per-vehicle keys are:
- `fs:{vid}:b60` and `fs:{vid}:b900`: hashes with **one packed field per bucket**. The field name
  is `{bucket_idx}`; the value is a JSON object `{stat: value}`, with numbers stored as `%.17g`
  strings so doubles round-trip exactly, and first/last values as `[ts, seq, value]`.
- `fs:{vid}:i60` and `fs:{vid}:i900`: zsets of live bucket indices, used for pruning.
- `fs:{vid}:meta`: max_event_ts, first_event_ts, last_snapshot_ts and events_applied.
- `fs:{vid}:latest:{component}`: the latest snapshot as JSON.
- `fs:snapshots`: a capped Redis Stream. Each new snapshot is appended here, and the risk scorer
  consumes it without polling.

Wall-clock TTL is 7 days and is refreshed on write. Simulated-time retention is enforced by pruning
after each snapshot, not by TTL, because accelerated live runs make wall-clock TTLs meaningless.

One Lua script per event (`services/stream-processor/stream_processor/features/lua/apply_event.lua`)
atomically:
1. runs `SET dedup:{vid}:{seq} NX EX <dedup ttl>`, and returns 0 without changing anything if the key
   already exists;
2. otherwise applies every bucket update and updates the meta hash.

Replaying an event within the dedup TTL therefore never double counts.

**Measured memory.** `scripts/verify_feature_parity.py`, run on 20 vehicles with full windows:
- Packed-bucket layout (current): mean 138,927 bytes per vehicle (max 159,891). Extrapolated
  linearly to 100K vehicles, that is about 14 GB.
- Original one-field-per-statistic layout: 357,862 bytes per vehicle, about 36 GB.

Dedup keys come on top: roughly 100 bytes per event, held for the 15-minute dedup TTL.

**Throughput.** The packed layout cuts the Redis calls per event from about 150 to about 12. The
apply rate went from 784 to 1,162 events/s per process on one laptop.

Mitigations, in order of expected effect:
1. Done: each bucket's statistics are packed into one field, which gave 2.6x less memory. The next
   step is a fixed-layout binary array instead of JSON.
2. Keep 60 s buckets only for the last hour and fold them into 900 s buckets on expiry. The 5m and
   1h windows only need 60 of them.
3. Drop statistics that no Phase 5 model uses, after feature selection.
4. Shard vehicles across a Redis Cluster; the key design is already per-vehicle.

## 7. Known limitations

- **Replay beyond the dedup TTL** (15 min wall clock) double counts, because the dedup key has
  expired. Recovery: rebuild the snapshots offline with `scripts/build_features_offline.py`, then
  reload the streaming state with `scripts/warm_feature_state.py`.
- **Late events.** Events older than max_event_ts - 25 h are counted in `late_events_ignored` and are
  not applied. Events that are late but within retention are applied to their buckets; snapshots
  already written are not revised. Full out-of-order handling is Phase 7.
- **Service context freshness.** The streaming engine refreshes its Postgres context every 60 s of
  wall clock. At high live speedups, a service recorded moments before a snapshot can be missed by
  that snapshot. The offline reference and the parity replay always see complete service history.
