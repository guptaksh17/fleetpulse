"""
Simulation Clock and Pacing (Phase 3).
Computes sim_time = start_time + step_index * step_seconds.
Supports offline fast-forward and live paced execution with speedup calculation.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("simulator.clock")


class SimulationClock:
    def __init__(
        self,
        start_time_iso: str = "2026-01-01T00:00:00Z",
        step_seconds: int = 60,
        emit_interval_seconds: float = 1.0,
        initial_step: int = 0,
    ):
        clean_iso = start_time_iso.replace("Z", "+00:00")
        self.start_time = datetime.fromisoformat(clean_iso)
        if self.start_time.tzinfo is None:
            self.start_time = self.start_time.replace(tzinfo=timezone.utc)
        self.step_seconds = step_seconds
        self.emit_interval_seconds = emit_interval_seconds
        self.step_index = initial_step
        self.dt_hours = float(step_seconds) / 3600.0

    @property
    def speedup(self) -> float:
        """Ratio of simulated time elapsed per wall-clock second in live mode."""
        if self.emit_interval_seconds <= 0:
            return 1.0
        return float(self.step_seconds) / float(self.emit_interval_seconds)

    def current_time(self) -> datetime:
        return self.start_time + timedelta(seconds=self.step_index * self.step_seconds)

    def current_time_iso(self) -> str:
        dt = self.current_time()
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def advance(self):
        self.step_index += 1

    def pace_live(self, elapsed_real_seconds: float):
        """Sleeps remainder of emit interval to maintain paced live speedup."""
        remaining = self.emit_interval_seconds - elapsed_real_seconds
        if remaining > 0:
            time.sleep(remaining)

    def get_state(self) -> dict:
        return {
            "start_time": self.start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "step_seconds": self.step_seconds,
            "emit_interval_seconds": self.emit_interval_seconds,
            "step_index": self.step_index,
        }

    def set_state(self, state: dict):
        clean_iso = state["start_time"].replace("Z", "+00:00")
        self.start_time = datetime.fromisoformat(clean_iso)
        if self.start_time.tzinfo is None:
            self.start_time = self.start_time.replace(tzinfo=timezone.utc)
        self.step_seconds = state["step_seconds"]
        self.emit_interval_seconds = state.get("emit_interval_seconds", 1.0)
        self.step_index = state["step_index"]
        self.dt_hours = float(self.step_seconds) / 3600.0
