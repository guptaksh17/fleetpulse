"""
Simulation Configuration and Deterministic RNG Management (Phase 3).
Loads YAML configs, applies environment variable overrides, calculates config hash,
and initializes 5 independent PCG64 generators spawned from a single SeedSequence.
"""

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import numpy as np

try:
    import yaml
except ImportError:
    yaml = None


@dataclass
class PopulationConfig:
    vehicles: int = 500
    tenants: int = 2
    fleets_per_tenant: int = 3
    vehicle_types: Dict[str, float] = field(
        default_factory=lambda: {"ICE": 0.45, "EV": 0.35, "HYBRID": 0.20}
    )
    driving_profiles: Dict[str, float] = field(
        default_factory=lambda: {"CITY": 0.40, "HIGHWAY": 0.25, "MIXED": 0.35}
    )
    oem: str = "OEM_A"
    home_center: List[float] = field(default_factory=lambda: [13.0827, 80.2707])
    home_radius_km: float = 25.0
    vin: Optional[str] = None
    # First VIN serial number of this population. VINs (and therefore vehicle ids) are derived
    # from the serial, not the seed, so separate populations need disjoint serial ranges.
    vin_serial_start: int = 1000
    # Share of vehicles connected through OEM-B (assigned by a hash of the VIN, not the RNG,
    # so enabling it never changes any other simulated value).
    oem_b_percent: float = 0.0


@dataclass
class ClockConfig:
    start_time: str = "2026-01-01T00:00:00Z"
    step_seconds: int = 60
    parked_heartbeat_seconds: int = 900
    days: int = 90
    emit_interval_seconds: float = 1.0


@dataclass
class DegradationThresholds:
    healthy_min: float = 75.0
    degrading_min: float = 50.0
    at_risk_min: float = 25.0


DEFAULT_BASE_WEAR_PER_HOUR = {"BRAKE": 0.05, "POWERTRAIN": 0.045, "BATTERY": 0.035}
DEFAULT_SUDDEN_FAILURE_PER_HOUR = {"BRAKE": 0.00018, "POWERTRAIN": 0.00018, "BATTERY": 0.00015}
DEFAULT_EFFECTS = {
    # BRAKE: effective deceleration a_eff = a_dem * (1 - k * wear/100)
    "brake_decel_deficit_at_full_wear": 0.35,
    "brake_thermal_mass_loss_at_full_wear": 0.5,
    # POWERTRAIN: coolant / motor temperature offset = wear * k (C)
    "powertrain_temp_offset_per_wear_c": 0.25,
    "powertrain_consumption_increase_at_full_wear": 0.15,
    # BATTERY: capacity retention and internal resistance
    "battery_retention_loss_at_full_wear": 0.30,
    "battery_resistance_rise_at_full_wear": 1.5,
}


def _per_component(raw, defaults: Dict[str, float]) -> Dict[str, float]:
    """Accepts a scalar (applied to every component) or a per-component dict."""
    if raw is None:
        return dict(defaults)
    if isinstance(raw, dict):
        out = dict(defaults)
        out.update({k.upper(): float(v) for k, v in raw.items()})
        return out
    return {k: float(raw) for k in defaults}


@dataclass
class DegradationConfig:
    scale: float = 1.0
    thresholds: DegradationThresholds = field(default_factory=DegradationThresholds)
    sudden_failure_per_hour_at_risk: Dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_SUDDEN_FAILURE_PER_HOUR)
    )
    base_wear_per_hour: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_BASE_WEAR_PER_HOUR))
    # Multiplies the whole wear increment (base + stress) per component, on top of `scale`.
    component_scale: Dict[str, float] = field(default_factory=lambda: {"BRAKE": 1.0, "POWERTRAIN": 1.0, "BATTERY": 1.0})
    service_delay_hours: List[float] = field(default_factory=lambda: [24.0, 72.0])
    service_recovery_factor: List[float] = field(default_factory=lambda: [0.15, 0.25])
    initial_wear_min: float = 2.0
    initial_wear_max: float = 35.0
    wear_rate_lognormal_sigma: float = 0.3
    effects: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_EFFECTS))

    @property
    def threshold_tuple(self) -> tuple:
        t = self.thresholds
        return (t.healthy_min, t.degrading_min, t.at_risk_min)


@dataclass
class SimConfig:
    population: PopulationConfig = field(default_factory=PopulationConfig)
    clock: ClockConfig = field(default_factory=ClockConfig)
    degradation: DegradationConfig = field(default_factory=DegradationConfig)
    seed: int = 42
    legacy: bool = False

    def to_dict(self) -> dict:
        return {
            "population": {
                "vehicles": self.population.vehicles,
                "tenants": self.population.tenants,
                "fleets_per_tenant": self.population.fleets_per_tenant,
                "vehicle_types": dict(self.population.vehicle_types),
                "driving_profiles": dict(self.population.driving_profiles),
                "oem": self.population.oem,
                "home_center": list(self.population.home_center),
                "home_radius_km": self.population.home_radius_km,
                "vin": self.population.vin,
                "vin_serial_start": self.population.vin_serial_start,
                **({"oem_b_percent": self.population.oem_b_percent} if self.population.oem_b_percent else {}),
            },
            "clock": {
                "start_time": self.clock.start_time,
                "step_seconds": self.clock.step_seconds,
                "parked_heartbeat_seconds": self.clock.parked_heartbeat_seconds,
                "days": self.clock.days,
                "emit_interval_seconds": self.clock.emit_interval_seconds,
            },
            "degradation": {
                "scale": self.degradation.scale,
                "thresholds": {
                    "healthy_min": self.degradation.thresholds.healthy_min,
                    "degrading_min": self.degradation.thresholds.degrading_min,
                    "at_risk_min": self.degradation.thresholds.at_risk_min,
                },
                "sudden_failure_per_hour_at_risk": dict(self.degradation.sudden_failure_per_hour_at_risk),
                "base_wear_per_hour": dict(self.degradation.base_wear_per_hour),
                "component_scale": dict(self.degradation.component_scale),
                "service_delay_hours": list(self.degradation.service_delay_hours),
                "service_recovery_factor": list(self.degradation.service_recovery_factor),
                "initial_wear_min": self.degradation.initial_wear_min,
                "initial_wear_max": self.degradation.initial_wear_max,
                "wear_rate_lognormal_sigma": self.degradation.wear_rate_lognormal_sigma,
                "effects": dict(self.degradation.effects),
            },
            "seed": self.seed,
            "legacy": self.legacy,
        }

    def compute_hash(self) -> str:
        canonical_json = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    @property
    def config_hash(self) -> str:
        return self.compute_hash()


class SimulationRNG:
    """Encapsulates 5 independent generators spawned from one SeedSequence."""

    def __init__(self, seed: int):
        self.seed = seed
        self.seed_seq = np.random.SeedSequence(seed)
        children = self.seed_seq.spawn(5)
        self.population = np.random.default_rng(children[0])
        self.driving = np.random.default_rng(children[1])
        self.degradation = np.random.default_rng(children[2])
        self.faults = np.random.default_rng(children[3])
        self.noise = np.random.default_rng(children[4])

    def get_all(self) -> dict:
        return {
            "population": self.population,
            "driving": self.driving,
            "degradation": self.degradation,
            "faults": self.faults,
            "noise": self.noise,
        }

    def get_states(self) -> dict:
        return {
            "population": self.population.bit_generator.state,
            "driving": self.driving.bit_generator.state,
            "degradation": self.degradation.bit_generator.state,
            "faults": self.faults.bit_generator.state,
            "noise": self.noise.bit_generator.state,
        }

    def set_states(self, states: dict):
        if "population" in states:
            self.population.bit_generator.state = states["population"]
        if "driving" in states:
            self.driving.bit_generator.state = states["driving"]
        if "degradation" in states:
            self.degradation.bit_generator.state = states["degradation"]
        if "faults" in states:
            self.faults.bit_generator.state = states["faults"]
        if "noise" in states:
            self.noise.bit_generator.state = states["noise"]


def load_config(config_path: Optional[str] = None) -> SimConfig:
    raw = {}
    if config_path and os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            if yaml:
                raw = yaml.safe_load(f) or {}
            else:
                # Basic JSON fallback if pyyaml absent
                raw = json.load(f)

    # Defaults
    pop_raw = raw.get("population", {})
    clock_raw = raw.get("clock", {})
    deg_raw = raw.get("degradation", {})
    thresh_raw = deg_raw.get("thresholds", {})

    cfg = SimConfig(
        population=PopulationConfig(
            vehicles=int(os.getenv("SIM_VEHICLES", pop_raw.get("vehicles", 500))),
            tenants=int(pop_raw.get("tenants", 2)),
            fleets_per_tenant=int(pop_raw.get("fleets_per_tenant", 3)),
            vehicle_types=pop_raw.get(
                "vehicle_types", {"ICE": 0.45, "EV": 0.35, "HYBRID": 0.20}
            ),
            driving_profiles=pop_raw.get(
                "driving_profiles", {"CITY": 0.40, "HIGHWAY": 0.25, "MIXED": 0.35}
            ),
            oem=pop_raw.get("oem", "OEM_A"),
            home_center=pop_raw.get("home_center", [13.0827, 80.2707]),
            home_radius_km=float(pop_raw.get("home_radius_km", 25.0)),
            vin=pop_raw.get("vin"),
            vin_serial_start=int(pop_raw.get("vin_serial_start", 1000)),
            oem_b_percent=float(pop_raw.get("oem_b_percent", 0.0)),
        ),
        clock=ClockConfig(
            start_time=os.getenv("SIM_START_TIME", clock_raw.get("start_time", "2026-01-01T00:00:00Z")),
            step_seconds=int(os.getenv("SIM_STEP_SECONDS", clock_raw.get("step_seconds", 60))),
            parked_heartbeat_seconds=int(
                os.getenv("SIM_PARKED_HEARTBEAT_SECONDS", clock_raw.get("parked_heartbeat_seconds", 900))
            ),
            days=int(os.getenv("SIM_DAYS", clock_raw.get("days", 90))),
            emit_interval_seconds=float(
                os.getenv("EMIT_INTERVAL_SEC", clock_raw.get("emit_interval_seconds", 1.0))
            ),
        ),
        degradation=DegradationConfig(
            scale=float(os.getenv("DEGRADATION_SCALE", deg_raw.get("scale", 1.0))),
            thresholds=DegradationThresholds(
                healthy_min=float(thresh_raw.get("healthy_min", 75.0)),
                degrading_min=float(thresh_raw.get("degrading_min", 50.0)),
                at_risk_min=float(thresh_raw.get("at_risk_min", 25.0)),
            ),
            sudden_failure_per_hour_at_risk=_per_component(
                deg_raw.get("sudden_failure_per_hour_at_risk"), DEFAULT_SUDDEN_FAILURE_PER_HOUR
            ),
            base_wear_per_hour=_per_component(deg_raw.get("base_wear_per_hour"), DEFAULT_BASE_WEAR_PER_HOUR),
            component_scale=_per_component(deg_raw.get("component_scale"), {"BRAKE": 1.0, "POWERTRAIN": 1.0, "BATTERY": 1.0}),
            service_delay_hours=[float(x) for x in deg_raw.get("service_delay_hours", [24.0, 72.0])],
            service_recovery_factor=[float(x) for x in deg_raw.get("service_recovery_factor", [0.15, 0.25])],
            initial_wear_min=float(deg_raw.get("initial_wear_min", 2.0)),
            initial_wear_max=float(deg_raw.get("initial_wear_max", 35.0)),
            wear_rate_lognormal_sigma=float(deg_raw.get("wear_rate_lognormal_sigma", 0.3)),
            effects={**DEFAULT_EFFECTS, **{k: float(v) for k, v in (deg_raw.get("effects") or {}).items()}},
        ),
        seed=int(os.getenv("SIM_SEED", raw.get("seed", 42))),
        legacy=bool(raw.get("legacy", False)),
    )
    return cfg
