"""
Battery Component Model for Predictive Maintenance Simulator (EV & Hybrid only).
Simulates calendar and cycle aging, internal resistance increase, capacity retention drop,
voltage sag, thermal stress, and battery DTCs.
"""

from typing import Dict, Optional
import math
from datetime import datetime
from .base import BaseComponent
from .lifecycle import LifecycleState


class BatteryComponent(BaseComponent):
    def __init__(
        self,
        initial_health: float = 100.0,
        wear_rate_multiplier: float = 1.0,
        base_wear_per_hour: float = 0.035,
        nominal_capacity_kwh: float = 75.0,
        r0_ohms: float = 0.05,
        sudden_failure_per_hour_at_risk: float = 0.00015,
        service_delay_range: tuple = (24.0, 72.0),
        service_recovery_range: tuple = (0.15, 0.25),
        thresholds: tuple = (75.0, 50.0, 25.0),
        retention_loss_at_full_wear: float = 0.30,
        resistance_rise_at_full_wear: float = 1.5,
    ):
        stress_coefficients = {
            "fast_charge_count": 0.05,        # DC fast charge stress
            "high_temp_duration": 0.04,        # cell_temp > 40C
            "deep_discharge_duration": 0.03,   # SoC < 10%
            "efc_fraction": 0.02,              # Equivalent Full Cycles
        }
        super().__init__(
            component_type="BATTERY",
            initial_health=initial_health,
            wear_rate_multiplier=wear_rate_multiplier,
            base_wear_per_hour=base_wear_per_hour,
            stress_coefficients=stress_coefficients,
            sudden_failure_per_hour_at_risk=sudden_failure_per_hour_at_risk,
            service_delay_range=service_delay_range,
            service_recovery_range=service_recovery_range,
            thresholds=thresholds,
        )
        self.retention_loss_at_full_wear = retention_loss_at_full_wear
        self.resistance_rise_at_full_wear = resistance_rise_at_full_wear
        self.nominal_capacity_kwh = nominal_capacity_kwh
        self.r0_ohms = r0_ohms
        self.total_energy_throughput_kwh = 0.0

    @property
    def capacity_retention(self) -> float:
        """
        Fraction of original capacity remaining:
        capacity_retention = 1.0 - 0.30 * (wear / 100)
        """
        return max(0.6, 1.0 - self.retention_loss_at_full_wear * (self.wear / 100.0))

    @property
    def internal_resistance(self) -> float:
        """
        Internal resistance rises:
        R_int = R_0 * (1 + 1.5 * wear / 100)
        """
        return self.r0_ohms * (1.0 + self.resistance_rise_at_full_wear * (self.wear / 100.0))

    def compute_voltage_sag(self, current_amps: float) -> float:
        """
        Voltage sag under load: Delta_V = I * R_int
        """
        return current_amps * self.internal_resistance

    def compute_apparent_soc(self, true_soc: float) -> float:
        """
        Observable SoC representation given capacity degradation:
        SoC_apparent = true_soc / capacity_retention (clipped to 100.0)
        """
        return min(100.0, true_soc / self.capacity_retention)

    def check_persistent_dtc(self, current_temp_c: float = 30.0) -> Optional[str]:
        """
        Returns persistent DTC code if component is in degraded lifecycle states.
        Codes match contracts/dtc/dtc_registry.yaml:
        - BATT_TEMP_HIGH
        - BATT_VOLT_UNSTABLE
        """
        if self.lifecycle == LifecycleState.FAILURE:
            return "BATT_VOLT_UNSTABLE"
        elif self.lifecycle == LifecycleState.MAINTENANCE_REQUIRED:
            if current_temp_c > 45.0:
                return "BATT_TEMP_HIGH"
            return "BATT_VOLT_UNSTABLE"
        return None
