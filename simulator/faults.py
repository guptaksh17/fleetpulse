"""
Fault Injection and Diagnostic Trouble Code (DTC) Management for FleetPulse Simulator.
Manages transient sensor noise DTCs, persistent degradation DTCs, and explicit DTC injections.
Codes strictly conform to contracts/dtc/dtc_registry.yaml.
"""

from typing import List, Optional, Set
import random


# Valid DTC codes from contracts/dtc/dtc_registry.yaml
VALID_DTC_CODES = {
    "P0301",
    "P0562",
    "BATT_TEMP_HIGH",
    "BATT_VOLT_UNSTABLE",
    "BRAKE_WEAR_HIGH",
    "BRAKE_SYSTEM_CRITICAL",
}


class FaultManager:
    def __init__(
        self,
        transient_rate_per_hour: float = 0.001,
        injected_code: Optional[str] = None,
        inject_after_seconds: float = 0.0,
        inject_duration_seconds: float = 0.0,
    ):
        self.transient_rate_per_hour = transient_rate_per_hour
        self.injected_code = injected_code if injected_code in VALID_DTC_CODES else None
        self.inject_after_seconds = inject_after_seconds
        self.inject_duration_seconds = inject_duration_seconds

        self.transient_active_dtc: Optional[str] = None
        self.transient_steps_remaining: int = 0

    def get_active_dtcs(
        self,
        elapsed_seconds: float,
        dt_seconds: float,
        components: List,
        rng_faults,
    ) -> List[str]:
        """
        Determines the list of active DTCs for the current step.
        Combines persistent component DTCs, transient electrical noise DTCs, and manual injections.
        """
        active_codes: Set[str] = set()

        # 1. Persistent DTCs from degraded components
        for comp in components:
            if hasattr(comp, "check_persistent_dtc"):
                code = comp.check_persistent_dtc()
                if code and code in VALID_DTC_CODES:
                    active_codes.add(code)

        # 2. Transient DTC handling
        if self.transient_steps_remaining > 0:
            self.transient_steps_remaining -= 1
            if self.transient_active_dtc:
                active_codes.add(self.transient_active_dtc)
        else:
            self.transient_active_dtc = None
            # Check for new transient DTC
            p_transient = (self.transient_rate_per_hour * (dt_seconds / 3600.0))
            if float(rng_faults.random()) < p_transient:
                transient_code = str(rng_faults.choice(["P0562", "BATT_VOLT_UNSTABLE"]))
                self.transient_active_dtc = transient_code
                self.transient_steps_remaining = int(rng_faults.integers(1, 4))
                active_codes.add(transient_code)

        # 3. Manual injection (e.g. from test script)
        if self.injected_code and self.inject_duration_seconds > 0:
            if self.inject_after_seconds <= elapsed_seconds < (self.inject_after_seconds + self.inject_duration_seconds):
                active_codes.add(self.injected_code)

        return sorted(list(active_codes))

    def get_state(self) -> dict:
        return {
            "transient_active_dtc": self.transient_active_dtc,
            "transient_steps_remaining": self.transient_steps_remaining,
        }

    def set_state(self, state: dict):
        self.transient_active_dtc = state.get("transient_active_dtc")
        self.transient_steps_remaining = state.get("transient_steps_remaining", 0)
