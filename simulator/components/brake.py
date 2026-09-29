"""
Brake Component Model for Predictive Maintenance Simulator.
Simulates pad and rotor wear, deceleration degradation, brake thermal dynamics, and DTCs.
"""

from typing import Dict, Optional
import math
from datetime import datetime
from .base import BaseComponent
from .lifecycle import LifecycleState


class BrakeComponent(BaseComponent):
    def __init__(
        self,
        initial_health: float = 100.0,
        wear_rate_multiplier: float = 1.0,
        base_wear_per_hour: float = 0.05,
        harsh_brake_coeff: float = 0.05,
        high_speed_decel_coeff: float = 0.03,
        sudden_failure_per_hour_at_risk: float = 0.00018,
        service_delay_range: tuple = (24.0, 72.0),
        service_recovery_range: tuple = (0.15, 0.25),
        thresholds: tuple = (75.0, 50.0, 25.0),
        decel_deficit_at_full_wear: float = 0.35,
        thermal_mass_loss_at_full_wear: float = 0.5,
    ):
        stress_coefficients = {
            "harsh_brake_count": harsh_brake_coeff,
            "high_speed_decel_count": high_speed_decel_coeff,
        }
        super().__init__(
            component_type="BRAKE",
            initial_health=initial_health,
            wear_rate_multiplier=wear_rate_multiplier,
            base_wear_per_hour=base_wear_per_hour,
            stress_coefficients=stress_coefficients,
            sudden_failure_per_hour_at_risk=sudden_failure_per_hour_at_risk,
            service_delay_range=service_delay_range,
            service_recovery_range=service_recovery_range,
            thresholds=thresholds,
        )
        self.decel_deficit_at_full_wear = decel_deficit_at_full_wear
        self.thermal_mass_loss_at_full_wear = thermal_mass_loss_at_full_wear
        self.brake_temp_c: float = 35.0

    def compute_effective_deceleration(self, demanded_decel: float) -> float:
        """
        Scales down effective deceleration capability as brake wear increases:
        a_eff = a_demanded * (1 - k * wear / 100), k = decel_deficit_at_full_wear
        """
        factor = 1.0 - self.decel_deficit_at_full_wear * (self.wear / 100.0)
        return demanded_decel * max(0.4, factor)

    def update_temperature(
        self,
        ambient_c: float,
        speed_kmh: float,
        decel_magnitude: float,
        harsh_brake: bool,
        dt_seconds: float,
    ) -> float:
        """
        Brake temperature rises under deceleration and cools down toward ambient.
        Worn brakes have less thermal mass, heating up significantly faster:
        Delta_T = Energy / (Thermal_Mass * (1 - 0.5 * wear / 100))
        """
        cooling_rate = 0.035 * (1.0 + speed_kmh / 50.0)  # faster airflow cools faster
        self.brake_temp_c -= (self.brake_temp_c - ambient_c) * (1.0 - math.exp(-cooling_rate * dt_seconds))

        if decel_magnitude > 0.5:
            # Braking heating
            heat_input = decel_magnitude * (speed_kmh / 30.0) * (2.5 if harsh_brake else 1.0)
            thermal_mass_factor = max(0.5, 1.0 - self.thermal_mass_loss_at_full_wear * (self.wear / 100.0))
            delta_t = (heat_input * dt_seconds * 0.15) / thermal_mass_factor
            self.brake_temp_c += delta_t

        self.brake_temp_c = max(ambient_c, min(650.0, self.brake_temp_c))
        return self.brake_temp_c

    def check_persistent_dtc(self) -> Optional[str]:
        """
        Returns persistent DTC code if component is in degraded lifecycle states.
        Codes match contracts/dtc/dtc_registry.yaml:
        - BRAKE_SYSTEM_CRITICAL when in FAILURE
        - BRAKE_WEAR_HIGH when in MAINTENANCE_REQUIRED
        """
        if self.lifecycle == LifecycleState.FAILURE:
            return "BRAKE_SYSTEM_CRITICAL"
        elif self.lifecycle == LifecycleState.MAINTENANCE_REQUIRED:
            return "BRAKE_WEAR_HIGH"
        return None

    def get_state(self) -> dict:
        state = super().get_state()
        state["brake_temp_c"] = self.brake_temp_c
        return state

    def set_state(self, state: dict):
        super().set_state(state)
        self.brake_temp_c = state.get("brake_temp_c", 35.0)
