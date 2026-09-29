"""
Shared fleet simulation engine.

One step loop used by the offline history generator, the live Kafka runner and the
calibration script, so all three produce identical telemetry and ground truth for the
same config and seed. Simulated time always starts at config.clock.start_time.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from typing import Callable, Dict, List, Optional, Tuple

from .config import SimConfig, SimulationRNG
from .population import FleetPopulation, SimVehicle
from .components import MaintenanceEventRecord
from .trips import TripRecord

logger = logging.getLogger("simulator.engine")

CHECKPOINT_VERSION = "2.0"


def parse_utc(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class StepOutput:
    vehicle: SimVehicle
    payload: dict
    events: List[MaintenanceEventRecord]
    trip: Optional[TripRecord]


class FleetEngine:
    def __init__(self, config: SimConfig, legacy_mode: bool = False):
        self.config = config
        self.rng = SimulationRNG(config.seed)
        self.vehicles: List[SimVehicle] = FleetPopulation.generate_population(
            config=config,
            rng_population=self.rng.population,
            legacy_mode=legacy_mode,
        )
        self.start_time = parse_utc(config.clock.start_time)
        self.step_seconds = int(config.clock.step_seconds)
        self.heartbeat_seconds = int(config.clock.parked_heartbeat_seconds)
        self.heartbeat_steps = max(1, self.heartbeat_seconds // self.step_seconds)
        self.step_index = 0
        self.current_date = None
        self.daily_slots: Dict[str, List[Tuple[datetime, datetime]]] = {}

    @property
    def sim_time(self) -> datetime:
        return self.start_time + timedelta(seconds=self.step_index * self.step_seconds)

    def step(self) -> List[StepOutput]:
        """Advances the whole fleet by one step and returns the emitted records."""
        now = self.sim_time
        if now.date() != self.current_date:
            self.current_date = now.date()
            self.daily_slots = {
                v.vehicle_id: v.trip_scheduler.get_trip_slots_for_date(self.current_date, self.rng.driving)
                for v in self.vehicles
            }

        is_heartbeat = (self.step_index % self.heartbeat_steps == 0)
        outputs: List[StepOutput] = []
        for v in self.vehicles:
            slots = self.daily_slots[v.vehicle_id]
            in_trip = any(s[0] <= now < s[1] for s in slots)
            if not in_trip and not is_heartbeat:
                continue
            dt_eff = self.step_seconds if in_trip else self.heartbeat_seconds
            payload, events, trip = v.step(
                dt_seconds=dt_eff,
                current_sim_time=now,
                rng_driving=self.rng.driving,
                rng_degradation=self.rng.degradation,
                rng_faults=self.rng.faults,
                rng_noise=self.rng.noise,
                scheduled_slots=slots,
                degradation_scale=self.config.degradation.scale,
                elapsed_seconds=self.step_index * self.step_seconds,
            )
            outputs.append(StepOutput(v, payload, events, trip))

        self.step_index += 1
        return outputs

    def run(
        self,
        until: datetime,
        on_step: Optional[Callable[[datetime, List[StepOutput]], None]] = None,
    ) -> int:
        """Steps until sim_time >= until. Returns the number of steps taken."""
        steps = 0
        while self.sim_time < until:
            t = self.sim_time
            outputs = self.step()
            if on_step:
                on_step(t, outputs)
            steps += 1
        return steps

    # ------------------------------------------------------------------
    # Checkpointing
    # ------------------------------------------------------------------
    def get_state(self) -> dict:
        return {
            "version": CHECKPOINT_VERSION,
            "config_hash": self.config.config_hash,
            "start_time": self.start_time.isoformat(),
            "step_index": self.step_index,
            "sim_time": self.sim_time.isoformat(),
            "current_date": self.current_date.isoformat() if self.current_date else None,
            "daily_slots": {
                vid: [[a.isoformat(), b.isoformat()] for a, b in slots]
                for vid, slots in self.daily_slots.items()
            },
            "rng_states": self.rng.get_states(),
            "vehicles": [v.get_state() for v in self.vehicles],
        }

    def set_state(self, state: dict):
        if state.get("config_hash") and state["config_hash"] != self.config.config_hash:
            logger.warning(
                "Checkpoint config hash %s differs from current config %s.",
                state["config_hash"][:8],
                self.config.config_hash[:8],
            )
        self.start_time = parse_utc(state["start_time"])
        self.step_index = int(state["step_index"])
        cd = state.get("current_date")
        self.current_date = datetime.fromisoformat(cd).date() if cd else None
        self.daily_slots = {
            vid: [(parse_utc(a), parse_utc(b)) for a, b in slots]
            for vid, slots in state.get("daily_slots", {}).items()
        }
        self.rng.set_states(state["rng_states"])
        by_id = {v.vehicle_id: v for v in self.vehicles}
        for v_state in state.get("vehicles", []):
            v = by_id.get(v_state["vehicle_id"])
            if v:
                v.set_state(v_state)

    def save_checkpoint(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = f"{path}.tmp"
        state = self.get_state()
        state["saved_at"] = datetime.now(timezone.utc).isoformat()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, path)
        logger.info("Saved checkpoint %s (step %d, sim_time %s)", path, self.step_index, self.sim_time)

    def load_checkpoint(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
        if state.get("version") != CHECKPOINT_VERSION:
            raise ValueError(f"Unsupported checkpoint version {state.get('version')} (expected {CHECKPOINT_VERSION})")
        self.set_state(state)
        logger.info("Restored checkpoint %s (step %d, sim_time %s)", path, self.step_index, self.sim_time)
