"""
Base Component Model for Predictive Maintenance Simulator (Phase 3).
Encapsulates hidden degradation state, stress updates, and lifecycle transitions.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional
import numpy as np

from .lifecycle import LifecycleState, MaintenanceEventRecord


class BaseComponent:
    def __init__(
        self,
        component_type: str,
        initial_health: float,
        wear_rate_multiplier: float,
        base_wear_per_hour: float,
        stress_coefficients: Dict[str, float],
        sudden_failure_per_hour_at_risk: float = 0.004,
        service_delay_range: tuple = (24.0, 72.0),
        service_recovery_range: tuple = (0.15, 0.25),
        thresholds: tuple = (75.0, 50.0, 25.0),
    ):
        self.component_type = component_type
        self.thresholds = tuple(float(x) for x in thresholds)
        self.health = float(max(0.0, min(100.0, initial_health)))
        self.wear = 100.0 - self.health
        self.wear_rate_multiplier = wear_rate_multiplier
        self.base_wear_per_hour = base_wear_per_hour
        self.stress_coefficients = stress_coefficients

        self.sudden_failure_per_hour_at_risk = sudden_failure_per_hour_at_risk
        self.service_delay_range = service_delay_range
        self.service_recovery_range = service_recovery_range

        self.lifecycle = self._compute_lifecycle(self.health)
        self.pending_service = False
        self.active_event: Optional[str] = None
        self.last_service_ts: Optional[datetime] = None
        self.service_due_ts: Optional[datetime] = None
        self.cumulative_distance_km = 0.0
        self.operating_hours = 0.0

    def _compute_lifecycle(self, health: float) -> LifecycleState:
        healthy_min, degrading_min, at_risk_min = self.thresholds
        if health > healthy_min:
            return LifecycleState.HEALTHY
        elif health > degrading_min:
            return LifecycleState.DEGRADING
        elif health > at_risk_min:
            return LifecycleState.AT_RISK
        else:
            return LifecycleState.MAINTENANCE_REQUIRED

    def step(
        self,
        dt_hours: float,
        distance_km: float,
        stress_inputs: Dict[str, float],
        current_sim_time: datetime,
        rng_degradation,
        degradation_scale: float = 1.0,
    ) -> List[MaintenanceEventRecord]:
        """
        Advances component state by dt_hours.
        Returns newly triggered maintenance records (if any).
        """
        events: List[MaintenanceEventRecord] = []

        # Case 1: Currently awaiting scheduled service
        if self.pending_service:
            if self.service_due_ts and current_sim_time >= self.service_due_ts:
                # Service complete!
                rec_factor = float(
                    rng_degradation.uniform(
                        self.service_recovery_range[0], self.service_recovery_range[1]
                    )
                )
                # Wear is partially recovered (never zero)
                self.wear = max(1.0, self.wear * rec_factor)
                self.health = max(0.0, min(100.0, 100.0 - self.wear))
                self.last_service_ts = current_sim_time
                self.service_due_ts = None
                self.pending_service = False
                self.active_event = None
                self.lifecycle = self._compute_lifecycle(self.health)

                events.append(
                    MaintenanceEventRecord(
                        event_type="SERVICE_COMPLETED",
                        occurred_at=current_sim_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        metadata={
                            "component": self.component_type,
                            "health_after": round(self.health, 2),
                            "wear_after": round(self.wear, 2),
                            "recovery_factor": round(rec_factor, 3),
                        },
                    )
                )
            # Wear does NOT accumulate while awaiting service
            return events

        # Case 2: Normal operation (wear accumulates)
        base_term = self.base_wear_per_hour * dt_hours * self.wear_rate_multiplier
        stress_sum = 0.0
        for stress_key, coeff in self.stress_coefficients.items():
            val = stress_inputs.get(stress_key, 0.0)
            stress_sum += coeff * val

        delta_wear = (base_term + stress_sum) * degradation_scale
        self.wear = min(100.0, self.wear + delta_wear)
        self.health = max(0.0, 100.0 - self.wear)
        self.cumulative_distance_km += distance_km
        self.operating_hours += dt_hours

        # Sudden failure check from AT_RISK only
        if self.lifecycle == LifecycleState.AT_RISK:
            # Hourly hazard rate scaled by stress
            stress_multiplier = max(1.0, 1.0 + stress_sum * 5.0)
            p_failure = 1.0 - (1.0 - min(0.05, self.sudden_failure_per_hour_at_risk * stress_multiplier)) ** max(0.01, dt_hours)
            if float(rng_degradation.random()) < p_failure:
                self.lifecycle = LifecycleState.FAILURE
                self.active_event = "FAILURE"
                self.pending_service = True
                delay_hrs = float(
                    rng_degradation.uniform(
                        self.service_delay_range[0], self.service_delay_range[1]
                    )
                )
                self.service_due_ts = current_sim_time + timedelta(hours=delay_hrs)

                events.append(
                    MaintenanceEventRecord(
                        event_type="FAILURE",
                        occurred_at=current_sim_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        metadata={
                            "component": self.component_type,
                            "health": round(self.health, 2),
                            "wear": round(self.wear, 2),
                            "delay_hours": round(delay_hrs, 2),
                            "failure_type": "SUDDEN_FAILURE",
                        },
                    )
                )
                return events

        # Check threshold boundaries
        healthy_min, degrading_min, at_risk_min = self.thresholds
        if self.health <= at_risk_min:
            if self.lifecycle != LifecycleState.MAINTENANCE_REQUIRED:
                self.lifecycle = LifecycleState.MAINTENANCE_REQUIRED
                self.active_event = "MAINTENANCE_REQUIRED"
                self.pending_service = True
                delay_hrs = float(
                    rng_degradation.uniform(
                        self.service_delay_range[0], self.service_delay_range[1]
                    )
                )
                self.service_due_ts = current_sim_time + timedelta(hours=delay_hrs)

                events.append(
                    MaintenanceEventRecord(
                        event_type="MAINTENANCE_REQUIRED",
                        occurred_at=current_sim_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        metadata={
                            "component": self.component_type,
                            "health": round(self.health, 2),
                            "wear": round(self.wear, 2),
                            "delay_hours": round(delay_hrs, 2),
                        },
                    )
                )
        elif self.health <= degrading_min:
            self.lifecycle = LifecycleState.AT_RISK
        elif self.health <= healthy_min:
            self.lifecycle = LifecycleState.DEGRADING
        else:
            self.lifecycle = LifecycleState.HEALTHY

        return events

    def get_state(self) -> dict:
        return {
            "component_type": self.component_type,
            "health": self.health,
            "wear": self.wear,
            "wear_rate_multiplier": self.wear_rate_multiplier,
            "lifecycle": self.lifecycle.value,
            "pending_service": self.pending_service,
            "active_event": self.active_event,
            "last_service_ts": self.last_service_ts.isoformat() if self.last_service_ts else None,
            "service_due_ts": self.service_due_ts.isoformat() if self.service_due_ts else None,
            "cumulative_distance_km": self.cumulative_distance_km,
            "operating_hours": self.operating_hours,
        }

    def set_state(self, state: dict):
        self.health = state["health"]
        self.wear = state["wear"]
        self.wear_rate_multiplier = state["wear_rate_multiplier"]
        self.lifecycle = LifecycleState(state["lifecycle"])
        self.pending_service = state["pending_service"]
        self.active_event = state.get("active_event")
        self.last_service_ts = (
            datetime.fromisoformat(state["last_service_ts"]) if state.get("last_service_ts") else None
        )
        self.service_due_ts = (
            datetime.fromisoformat(state["service_due_ts"]) if state.get("service_due_ts") else None
        )
        self.cumulative_distance_km = state.get("cumulative_distance_km", 0.0)
        self.operating_hours = state.get("operating_hours", 0.0)
