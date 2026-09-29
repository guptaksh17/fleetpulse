"""
Component Lifecycle State Machine (Phase 3).
States: HEALTHY, DEGRADING, AT_RISK, MAINTENANCE_REQUIRED, FAILURE.
Enforces:
  - Threshold boundaries (configurable, default 75, 50, 25).
  - Sudden failures only transition from AT_RISK.
  - Exactly one maintenance event per episode.
  - SERVICE_COMPLETED follows service delay.
  - Wear reset never returns to zero (recovery factor in [0.15, 0.25]).
  - Wear accumulation halts while awaiting service.
"""

from enum import Enum
from typing import Optional
import uuid

EVENT_ID_NAMESPACE = uuid.UUID("5b0c1d0e-6f1e-4e57-9a55-6d61696e7465")


class LifecycleState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADING = "DEGRADING"
    AT_RISK = "AT_RISK"
    MAINTENANCE_REQUIRED = "MAINTENANCE_REQUIRED"
    FAILURE = "FAILURE"


class MaintenanceEventRecord:
    """
    Ground-truth service record. The vehicle fills in vehicle_id,
    vehicle_component_id and odometer_km when the component emits the event.
    metadata may carry hidden state (health, wear) for offline analysis only;
    it is stored in Postgres and never used as a feature input.
    """

    def __init__(
        self,
        event_type: str,
        occurred_at: str,
        metadata: dict,
        component: Optional[str] = None,
        vehicle_component_id: Optional[str] = None,
        vehicle_id: Optional[str] = None,
        odometer_km: Optional[float] = None,
    ):
        self.event_type = event_type
        self.occurred_at = occurred_at
        self.metadata = metadata
        self.component = component or (metadata or {}).get("component")
        self.vehicle_component_id = vehicle_component_id
        self.vehicle_id = vehicle_id
        self.odometer_km = odometer_km

    @property
    def event_id(self) -> str:
        """Deterministic id so replays and reruns are idempotent (ON CONFLICT)."""
        if not self.vehicle_component_id:
            raise ValueError("event_id requires vehicle_component_id to be set")
        key = f"{self.vehicle_component_id}|{self.event_type}|{self.occurred_at}"
        return str(uuid.uuid5(EVENT_ID_NAMESPACE, key))

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id if self.vehicle_component_id else None,
            "vehicle_id": self.vehicle_id,
            "vehicle_component_id": self.vehicle_component_id,
            "component": self.component,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "odometer_km": self.odometer_km,
            "metadata": self.metadata,
        }
