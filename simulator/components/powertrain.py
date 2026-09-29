"""
Powertrain Component Model for Predictive Maintenance Simulator.
Supports both ICE and EV/Hybrid powertrains with thermal offsets, efficiency degradation,
torque shortfall, and DTC triggers.
"""

from typing import Dict, Optional
import math
from datetime import datetime
from .base import BaseComponent
from .lifecycle import LifecycleState


class PowertrainComponent(BaseComponent):
    def __init__(
        self,
        vehicle_type: str = "EV",  # "ICE", "EV", "HYBRID"
        initial_health: float = 100.0,
        wear_rate_multiplier: float = 1.0,
        base_wear_per_hour: float = 0.045,
        sudden_failure_per_hour_at_risk: float = 0.00018,
        service_delay_range: tuple = (24.0, 72.0),
        service_recovery_range: tuple = (0.15, 0.25),
        thresholds: tuple = (75.0, 50.0, 25.0),
        temp_offset_per_wear_c: float = 0.25,
        consumption_increase_at_full_wear: float = 0.15,
    ):
        self.temp_offset_per_wear_c = temp_offset_per_wear_c
        self.consumption_increase_at_full_wear = consumption_increase_at_full_wear
        self.vehicle_type = vehicle_type.upper()
        if self.vehicle_type == "ICE":
            stress_coefficients = {
                "high_temp_duration": 0.04,   # engine_temp > 105C
                "high_load_duration": 0.03,   # engine_load > 80%
                "high_rpm_duration": 0.02,    # rpm > 4500
            }
        else:
            stress_coefficients = {
                "high_temp_duration": 0.04,   # motor_temp > 90C
                "high_power_duration": 0.03,  # power_kw > 70% peak
            }

        super().__init__(
            component_type="POWERTRAIN",
            initial_health=initial_health,
            wear_rate_multiplier=wear_rate_multiplier,
            base_wear_per_hour=base_wear_per_hour,
            stress_coefficients=stress_coefficients,
            sudden_failure_per_hour_at_risk=sudden_failure_per_hour_at_risk,
            service_delay_range=service_delay_range,
            service_recovery_range=service_recovery_range,
            thresholds=thresholds,
        )

    def compute_temp_offset(self) -> float:
        """
        Observable perturbation: Coolant / motor temp runs hotter for equivalent load:
        T_offset = wear * temp_offset_per_wear_c
        """
        return self.wear * self.temp_offset_per_wear_c

    def compute_consumption_multiplier(self) -> float:
        """
        Observable perturbation: Fuel / energy consumption increases:
        rate_eff = rate_base * (1 + 0.15 * wear / 100)
        """
        return 1.0 + self.consumption_increase_at_full_wear * (self.wear / 100.0)

    def compute_torque_delivery(self, demanded_torque: float) -> float:
        """
        Observable perturbation: Torque delivery shortfall under full throttle:
        torque_act = torque_dem * (1 - 0.20 * wear / 100)
        """
        return demanded_torque * max(0.6, (1.0 - 0.20 * (self.wear / 100.0)))

    def check_persistent_dtc(self) -> Optional[str]:
        """
        Returns persistent DTC code if component is in degraded lifecycle states.
        Codes match contracts/dtc/dtc_registry.yaml:
        - For ICE: P0301 (misfire) or P0562 (system voltage low)
        - For EV/Hybrid: P0562
        """
        if self.lifecycle == LifecycleState.FAILURE:
            if self.vehicle_type == "ICE":
                return "P0301"
            else:
                return "P0562"
        elif self.lifecycle == LifecycleState.MAINTENANCE_REQUIRED:
            if self.vehicle_type == "ICE":
                return "P0301"
            else:
                return "P0562"
        return None
