"""
In-memory sink used by tests and the calibration script.
Implements the same interface as the Timescale and Postgres sinks.
"""

from typing import Dict, List


class InMemorySink:
    def __init__(self, keep_telemetry: bool = True):
        self.keep_telemetry = keep_telemetry
        self.telemetry: List[dict] = []
        self.telemetry_count = 0
        self.events: List = []
        self.trips: List = []

    def connect(self):
        pass

    def add_telemetry(self, vehicle, payload: Dict):
        self.telemetry_count += 1
        if self.keep_telemetry:
            self.telemetry.append({"vehicle_id": vehicle.vehicle_id, **payload})

    def save_maintenance_events(self, events):
        self.events.extend(events)

    def save_trips(self, trips):
        self.trips.extend(trips)

    def flush(self):
        pass

    def close(self):
        pass
