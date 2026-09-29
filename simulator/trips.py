"""
Trip Scheduling and Lifecycle Management for FleetPulse Simulator.
Schedules daily trips per vehicle based on driving profile, tracks active trips,
computes deterministic trip UUIDs, and generates trip records for PostgreSQL persistence.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, time, timezone
from typing import List, Optional, Tuple
import uuid

from .driving import DrivingProfile


@dataclass
class TripRecord:
    trip_id: str
    vehicle_id: str
    driver_id: Optional[str]
    started_at: datetime
    ended_at: datetime
    distance_km: float


class TripScheduler:
    def __init__(
        self,
        vehicle_id: str,
        driving_profile: DrivingProfile,
        driver_id: Optional[str] = None,
    ):
        self.vehicle_id = vehicle_id
        self.driving_profile = driving_profile
        self.driver_id = driver_id
        self.trip_counter: int = 0

        self.current_trip_id: Optional[str] = None
        self.current_trip_started_at: Optional[datetime] = None
        self.current_trip_distance_km: float = 0.0

    def get_trip_slots_for_date(self, date_val: datetime.date, rng_driving) -> List[Tuple[datetime, datetime]]:
        """
        Generates daily trip windows (start_time, end_time) for a specific date.
        """
        slots = []
        base_dt = datetime.combine(date_val, time.min, tzinfo=timezone.utc)

        if self.driving_profile == DrivingProfile.CITY:
            # 3 to 4 trips per day
            trip_starts_mins = [
                8 * 60 + int(rng_driving.integers(0, 30)),     # ~08:00 - 08:30
                11 * 60 + int(rng_driving.integers(15, 45)),   # ~11:15 - 11:45
                14 * 60 + int(rng_driving.integers(0, 30)),    # ~14:00 - 14:30
                17 * 60 + int(rng_driving.integers(15, 45)),   # ~17:15 - 17:45
            ]
            for start_min in trip_starts_mins:
                duration_min = int(rng_driving.integers(15, 35))
                t_start = base_dt + timedelta(minutes=start_min)
                t_end = t_start + timedelta(minutes=duration_min)
                slots.append((t_start, t_end))

        elif self.driving_profile == DrivingProfile.HIGHWAY:
            # 1 to 2 long trips per day
            trip_starts_mins = [
                6 * 60 + int(rng_driving.integers(30, 90)),    # ~07:00
                14 * 60 + int(rng_driving.integers(0, 60)),    # ~14:30
            ]
            for start_min in trip_starts_mins:
                duration_min = int(rng_driving.integers(120, 210))  # 2 - 3.5 hours
                t_start = base_dt + timedelta(minutes=start_min)
                t_end = t_start + timedelta(minutes=duration_min)
                slots.append((t_start, t_end))

        else:  # MIXED
            # 2 to 3 moderate trips per day
            trip_starts_mins = [
                8 * 60 + int(rng_driving.integers(15, 45)),    # ~08:30
                13 * 60 + int(rng_driving.integers(0, 30)),    # ~13:15
                18 * 60 + int(rng_driving.integers(0, 45)),    # ~18:15
            ]
            for start_min in trip_starts_mins:
                duration_min = int(rng_driving.integers(40, 75))   # 40 - 75 mins
                t_start = base_dt + timedelta(minutes=start_min)
                t_end = t_start + timedelta(minutes=duration_min)
                slots.append((t_start, t_end))

        return slots

    def check_trip_status(
        self,
        current_sim_time: datetime,
        step_distance_km: float,
        scheduled_slots: List[Tuple[datetime, datetime]],
    ) -> Tuple[bool, Optional[str], Optional[TripRecord]]:
        """
        Determines whether the vehicle is currently in a trip.
        Returns:
            is_in_trip (bool)
            trip_id (Optional[str])
            completed_trip (Optional[TripRecord])
        """
        in_slot = False
        for t_start, t_end in scheduled_slots:
            if t_start <= current_sim_time < t_end:
                in_slot = True
                break

        completed_record: Optional[TripRecord] = None

        if in_slot:
            if self.current_trip_id is None:
                # Starting a new trip
                self.trip_counter += 1
                self.current_trip_id = str(
                    uuid.uuid5(uuid.NAMESPACE_DNS, f"{self.vehicle_id}:{self.trip_counter}")
                )
                self.current_trip_started_at = current_sim_time
                self.current_trip_distance_km = step_distance_km
            else:
                self.current_trip_distance_km += step_distance_km

            return True, self.current_trip_id, None

        else:
            if self.current_trip_id is not None:
                # Just finished a trip
                completed_record = TripRecord(
                    trip_id=self.current_trip_id,
                    vehicle_id=self.vehicle_id,
                    driver_id=self.driver_id,
                    started_at=self.current_trip_started_at or current_sim_time,
                    ended_at=current_sim_time,
                    distance_km=round(self.current_trip_distance_km, 2),
                )
                self.current_trip_id = None
                self.current_trip_started_at = None
                self.current_trip_distance_km = 0.0

            return False, None, completed_record

    def get_state(self) -> dict:
        return {
            "trip_counter": self.trip_counter,
            "current_trip_id": self.current_trip_id,
            "current_trip_started_at": (
                self.current_trip_started_at.isoformat() if self.current_trip_started_at else None
            ),
            "current_trip_distance_km": self.current_trip_distance_km,
        }

    def set_state(self, state: dict):
        self.trip_counter = state.get("trip_counter", 0)
        self.current_trip_id = state.get("current_trip_id")
        self.current_trip_started_at = (
            datetime.fromisoformat(state["current_trip_started_at"])
            if state.get("current_trip_started_at")
            else None
        )
        self.current_trip_distance_km = state.get("current_trip_distance_km", 0.0)
