# FleetPulse Phase 3: Simulator Realism and Ground Truth Specification

## 1. Executive Architecture

The FleetPulse Phase 3 Telemetry and Ground Truth Simulation Platform expands the single-vehicle vertical slice into a high-fidelity synthetic fleet generator with hidden physical degradation, stochastically generated maintenance ground truth, and accelerated execution pacing.

```
                      +------------------------------------------+
                      |         Simulator (Hidden Truth)         |
                      |  - LifecycleState (Healthy...Failure)    |
                      |  - Component Wear (0 - 100%)             |
                      |  - Stress Accumulation & Hazard Rates    |
                      +--------------------+---------------------+
                                           |
                                           | Observable Perturbations ONLY
                                           | (Temperatures, Voltages, Decel, DTCs)
                                           v
                   +-----------------------------------------------+
                   |              Kafka / TimescaleDB              |
                   |      Strictly Zero Hidden State Leakage       |
                   +-----------------------------------------------+
```

### The Primary Governing Invariant
**The simulator owns hidden truth.** Kafka and the telemetry hypertable carry strictly observable physical signals. Health, wear, stress, and lifecycle state must **NEVER** appear in any Kafka topic or telemetry column. Ground-truth maintenance events (`MAINTENANCE_REQUIRED`, `FAILURE`, `SERVICE_COMPLETED`) and trips are persisted directly to PostgreSQL as service history records.

---

## 2. Demographic & Population Dynamics

The simulator models fleets of arbitrary size ($N \ge 1$, default $N=500$ across 3 fleets):

### Powertrain Architecture Proportions
- **ICE (Internal Combustion Engine)**: 50%
- **EV (Battery Electric Vehicle)**: 30%
- **Hybrid (Parallel HEV)**: 20%

### Driver Behaviors
- **Conservative** (40%): Gentle accelerations ($1.0 - 2.2 \text{ m/s}^2$), low cruising speeds ($0.90\times$), low harsh braking ($\lambda = 0.05/\text{hr}$).
- **Normal** (45%): Typical operational dynamics ($1.8 - 3.2 \text{ m/s}^2$), standard cruising speed ($1.00\times$), moderate harsh braking ($\lambda = 0.20/\text{hr}$).
- **Aggressive** (15%): Rapid accelerations ($2.8 - 4.8 \text{ m/s}^2$), higher speeds ($1.12\times$), frequent harsh braking ($\lambda = 0.85/\text{hr}$).

### Operational Driving Profiles
- **City Delivery** (35%): 3–4 short trips/day (15–35 min), frequent stop-and-go signals, heavy braking.
- **Highway Freight** (25%): 1–2 long trips/day (2–3.5 hours), steady cruising speed (70–115 km/h).
- **Mixed Regional** (40%): 2–3 moderate trips/day (40–75 min), balance of highway corridors and city streets.

### Initial Vehicle State Generation
- **Vehicle Age**: Lognormally distributed with median 2.5 years, bounded in $[0.1, 8.0]$ years.
- **Initial Odometer**: $\text{age\_years} \times \mathcal{U}(15000, 35000) \text{ km}$.
- **Initial Wear**: Calibrated as $\text{wear}_0 = \min(60.0, \max(2.0, \frac{\text{age}}{8.0} \times 56.0 + \mathcal{N}(0, 3.5)))$. No vehicle starts broken ($\le 60\%$).
- **Wear Rate Multiplier**: Sampled per vehicle from $\text{Lognormal}(\mu=0, \sigma=0.25)$.

---

## 3. Mathematical Models of Component Degradation

### Generic Discrete Update Step
At each simulation step $\Delta t$, wear increments according to:
$$\Delta w = \left( w_{\text{base}} \cdot \Delta t + \sum_{i} c_i \cdot \text{stress}_i \right) \cdot m_{\text{veh}} \cdot s_{\text{deg}}$$
Where:
- $w_{\text{base}}$: Base wear rate per operating hour.
- $c_i$: Stress sensitivity coefficients.
- $\text{stress}_i$: Step-level physical stressors.
- $m_{\text{veh}}$: Vehicle wear rate multiplier.
- $s_{\text{deg}}$: Global degradation scaling factor (e.g. 1.0).

---

### Component Models

#### 1. Brake System (`BRAKE`)
- **Stress Terms**:
  - Harsh brake occurrences: Poisson count $\mathcal{P}(\lambda_{\text{harsh}} \Delta t)$.
  - Decelerations from high speed ($> 80 \text{ km/h}$).
- **Observable Perturbations**:
  - Stopping capability degradation:
    $$a_{\text{eff}} = a_{\text{demanded}} \times \left(1.0 - 0.35 \times \frac{w}{100}\right)$$
  - Thermal mass loss: Rotor and pad thinning reduces heat capacity, increasing temperature rise:
    $$\Delta T_{\text{brake}} = \frac{E_{\text{brake}}}{C_{\text{thermal}} \times (1.0 - 0.5 \times \frac{w}{100})}$$
  - DTC Triggers: Emits `BRAKE_WEAR_HIGH` in `MAINTENANCE_REQUIRED`, `BRAKE_SYSTEM_CRITICAL` in `FAILURE`.

#### 2. Powertrain (`POWERTRAIN`)
- **Stress Terms**:
  - ICE: Sustained operating temperature $> 105^\circ\text{C}$, load $> 80\%$, RPM $> 4500$.
  - EV/Hybrid: Traction motor temperature $> 90^\circ\text{C}$, electrical power $> 60 \text{ kW}$.
- **Observable Perturbations**:
  - Coolant / Motor temperature offset:
    $$T_{\text{offset}} = w \times 0.25^\circ\text{C}$$
  - Energy / Fuel consumption increase:
    $$\text{rate}_{\text{eff}} = \text{rate}_{\text{base}} \times \left(1.0 + 0.15 \times \frac{w}{100}\right)$$
  - Torque delivery shortfall:
    $$\tau_{\text{act}} = \tau_{\text{dem}} \times \left(1.0 - 0.20 \times \frac{w}{100}\right)$$
  - DTC Triggers: Emits `P0301` (ICE misfire) or `P0562` (low system voltage).

#### 3. High-Voltage Battery (`BATTERY` - EV/Hybrid Only)
- **Stress Terms**:
  - Fast-charging cycles, cell temperature $> 40^\circ\text{C}$, deep discharge ($\text{SoC} < 10\%$).
- **Observable Perturbations**:
  - Capacity retention loss:
    $$\text{Retention} = 1.0 - 0.30 \times \frac{w}{100}$$
    $$\text{SoC}_{\text{apparent}} = \min\left(100.0, \frac{\text{SoC}_{\text{true}}}{\text{Retention}}\right)$$
  - Internal resistance ($R_{\text{int}}$) escalation:
    $$R_{\text{int}} = R_0 \times \left(1.0 + 1.5 \times \frac{w}{100}\right)$$
  - Voltage sag under load:
    $$\Delta V = I_{\text{pack}} \times R_{\text{int}}$$
  - DTC Triggers: Emits `BATT_TEMP_HIGH` or `BATT_VOLT_UNSTABLE`.

---

## 4. Lifecycle Transitions & Ground-Truth Maintenance

```
 +-------------------------------------------------------------------+
 |                                                                   |
 |   HEALTHY (Health > 75)                                           |
 |      |                                                            |
 |      v                                                            |
 |   DEGRADING (50 < Health <= 75)                                   |
 |      |                                                            |
 |      v                                                            |
 |   AT_RISK (25 < Health <= 50) -----(Sudden Failure Hazard)---->+  |
 |      |                                                         |  |
 |      v (Gradual Wear-Out)                                      |  |
 |   MAINTENANCE_REQUIRED (Health <= 25)                          v  |
 |      |                                                      FAILURE
 |      +----------------------->[ SERVICE DELAY ]<---------------+  |
 |                                      |                            |
 |                                      v (24-72h Sim Time)          |
 |                              SERVICE_COMPLETED                    |
 |                       (Wear drops to 15-25%, resets)              |
 +-------------------------------------------------------------------+
```

### Sudden Failure Hazard Formulation
Sudden failures can **only occur from the `AT_RISK` state**:
$$P(\text{failure in } \Delta t) = 1.0 - \left(1.0 - \min(0.05, h_{\text{base}} \cdot \text{stress\_mult})\right)^{\Delta t}$$
Where $h_{\text{base}} = 0.0004/\text{operating hour}$.

### Maintenance Scheduling and Restoration Invariants
1. When entering `MAINTENANCE_REQUIRED` or `FAILURE`, a record is persisted to PostgreSQL `maintenance_event`.
2. Service is scheduled with delay $\mathcal{U}(24, 72)$ hours of simulation time.
3. Wear **halts completely** while awaiting service.
4. When service completes, wear drops to $\mathcal{U}(15, 25)\%$, ensuring that components are renewed but **never reset to zero wear** (preserving realistic hysteresis).

---

## 5. Clock Architecture & Execution Modes

$$\text{sim\_time} = \text{start\_time} + \text{step\_index} \times \Delta t_{\text{step}}$$

### 1. Offline Mode (`scripts/generate_history.py`)
- High-speed batch processing without network overhead.
- Direct bulk insertion into TimescaleDB `telemetry` hypertable using multi-row batch inserts (`ON CONFLICT DO NOTHING`).
- Ground truth (`trip`, `maintenance_event`) written directly to PostgreSQL.
- Exports complete deterministic state snapshot to `--checkpoint-out`.

### 2. Live Mode (`simulator/runner_live.py`)
- Paces wall-clock time according to configured speedup: $\Delta t_{\text{wall}} = \Delta t_{\text{step}} / \text{speedup}$.
- Publishes OEM-A envelopes to Kafka topic `oem.inbound` (key = VIN).
- Ground truth persisted concurrently to PostgreSQL.
- Periodic checkpoints every 500 steps and on graceful shutdown (`SIGINT`/`SIGTERM`).

### 3. Single-Vehicle Legacy Mode (`simulator/vehicle_simulator.py`)
- Out-of-the-box fallback preserving 100% backward compatibility for Phase 1 & 2 integration tests (`verify_phase2.sh`).
