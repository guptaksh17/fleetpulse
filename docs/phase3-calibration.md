# FleetPulse Phase 3: Simulator Calibration Report

- Generated: 2026-09-28T21:01:12Z by `scripts/calibrate_degradation.py`
- Config: `configs/offline_train.yaml` (hash dfe84c835563), seed 42
- Fleet: 200 vehicles, 90 simulated days from 2026-01-01T00:00:00+00:00
- Simulation wall time: 103.8 s

Method: hourly snapshots per active vehicle component. Each snapshot holds the latest value of each
raw signal emitted strictly before t. label_7d = 1 if a MAINTENANCE_REQUIRED or FAILURE event of that
component occurs in (t, t + 7 d]. Snapshots taken while the component awaits service (in-gap) and
snapshots whose 7-day horizon extends past the end of the run are excluded. AUC is reported as
max(AUC, 1 - AUC) with the direction of the association.

## 1. Targets

| Check | Target | Value | Status |
|---|---|---|---|
| Max single-signal ROC-AUC | <= 0.85 | 0.6935 | PASS |
| Prevalence per vehicle-component (all) | 25 - 40 % | 38.97 % | PASS |
| Sudden failure ratio | < 15 % | 7.0 % | PASS |
| Prevalence BRAKE components | 25 - 40 % | 38.0 % | PASS |
| Prevalence POWERTRAIN components | 25 - 40 % | 39.5 % | PASS |
| Prevalence BATTERY components | 25 - 40 % | 39.81 % | PASS |
| Hourly positive rate BRAKE | 3 - 8 % | 3.211 % | PASS |
| Hourly positive rate POWERTRAIN | 3 - 8 % | 3.184 % | PASS |
| Hourly positive rate BATTERY | 3 - 8 % | 3.189 % | PASS |
| Prevalence per vehicle (informational) | n/a | 65.5 % | n/a |

## 2. Events

- Event counts: {"FAILURE": 14, "MAINTENANCE_REQUIRED": 186, "SERVICE_COMPLETED": 196}
- Sudden failures: 14
- MAINTENANCE_REQUIRED + FAILURE events per third of the run (stationarity): {"days_0_30": 67, "days_30_60": 76, "days_60_90": 57}

| Component | Active | With event | Prevalence % | Trigger events | Eligible snapshots | Positive snapshots | Positive rate % |
|---|---|---|---|---|---|---|---|
| BRAKE | 200 | 76 | 38.0 | 80 | 394932 | 12680 | 3.211 |
| POWERTRAIN | 200 | 79 | 39.5 | 79 | 394938 | 12574 | 3.184 |
| BATTERY | 103 | 41 | 39.81 | 41 | 203159 | 6479 | 3.189 |

## 3. Per-signal univariate ROC-AUC (latest hourly value)

| Signal | BRAKE | POWERTRAIN | BATTERY |
|---|---|---|---|
| speed_kmh | 0.5240 (higher) | 0.5231 (higher) | 0.5016 (higher) |
| acceleration_ms2 | 0.5003 (higher) | 0.5009 (higher) | 0.5008 (lower) |
| engine_temp_c | 0.5361 (higher) | 0.6264 (higher) | 0.5270 (lower) |
| motor_temp_c | 0.5765 (higher) | 0.6522 (higher) | 0.5022 (lower) |
| engine_load_pct | 0.5202 (higher) | 0.5245 (higher) | 0.5091 (higher) |
| rpm | 0.5202 (higher) | 0.5246 (higher) | 0.5092 (higher) |
| power_kw | 0.5134 (higher) | 0.5206 (higher) | 0.5014 (lower) |
| battery_temp_c | 0.5207 (higher) | 0.5351 (higher) | 0.5011 (higher) |
| voltage_v | 0.5249 (lower) | 0.5685 (lower) | 0.5200 (lower) |
| current_a | 0.5279 (higher) | 0.5574 (higher) | 0.5010 (lower) |
| soc_pct | 0.5236 (lower) | 0.5268 (lower) | 0.6935 (higher) |
| dtc_count | 0.5007 (higher) | 0.5054 (lower) | 0.5033 (higher) |

## 4. Degradation parameters used

```json
{
  "base_wear_per_hour": {
    "BATTERY": 0.035,
    "BRAKE": 0.05,
    "POWERTRAIN": 0.045
  },
  "component_scale": {
    "BATTERY": 0.25,
    "BRAKE": 1.1,
    "POWERTRAIN": 1.45
  },
  "effects": {
    "battery_resistance_rise_at_full_wear": 1.5,
    "battery_retention_loss_at_full_wear": 0.3,
    "brake_decel_deficit_at_full_wear": 0.35,
    "brake_thermal_mass_loss_at_full_wear": 0.5,
    "powertrain_consumption_increase_at_full_wear": 0.15,
    "powertrain_temp_offset_per_wear_c": 0.25
  },
  "initial_wear_max": 74.5,
  "initial_wear_min": 12.0,
  "scale": 1.0,
  "service_delay_hours": [
    24.0,
    72.0
  ],
  "service_recovery_factor": [
    0.15,
    0.25
  ],
  "sudden_failure_per_hour_at_risk": {
    "BATTERY": 1.5e-05,
    "BRAKE": 0.00018,
    "POWERTRAIN": 0.00018
  },
  "thresholds": {
    "at_risk_min": 25.0,
    "degrading_min": 50.0,
    "healthy_min": 75.0
  },
  "wear_rate_lognormal_sigma": 0.3
}
```
