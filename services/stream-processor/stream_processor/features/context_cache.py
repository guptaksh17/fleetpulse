"""
Vehicle context for snapshots, cached from PostgreSQL and refreshed periodically:
vehicle type, manufacture date, ACTIVE components and SERVICE_COMPLETED history
(occurred_at, odometer_km) per component. Only business records are read; nothing from
the simulator's hidden state is available here.
"""

from collections import defaultdict
from datetime import datetime, time as dtime, timezone
import logging
import time
from typing import Dict, Optional

logger = logging.getLogger("stream-processor.features.context")

VEHICLES_SQL = """
SELECT v.vehicle_id::text, v.vehicle_type, v.manufacture_date,
       array_agg(vc.component ORDER BY vc.component) FILTER (WHERE vc.status = 'ACTIVE')
FROM vehicle v
LEFT JOIN vehicle_component vc ON vc.vehicle_id = v.vehicle_id
GROUP BY v.vehicle_id, v.vehicle_type, v.manufacture_date
"""

SERVICES_SQL = """
SELECT vc.vehicle_id::text, vc.component, me.occurred_at, me.odometer_km
FROM maintenance_event me
JOIN vehicle_component vc ON vc.vehicle_component_id = me.vehicle_component_id
WHERE me.event_type = 'SERVICE_COMPLETED'
ORDER BY vc.vehicle_id, vc.component, me.occurred_at
"""


def _ms(dt: datetime) -> int:
    return int(round(dt.timestamp() * 1000))


def date_to_ms(d) -> Optional[int]:
    if d is None:
        return None
    return _ms(datetime.combine(d, dtime(0, 0), tzinfo=timezone.utc))


class StaticContextProvider:
    """Context from already-loaded rows (used by the parity and offline scripts, and by the cache)."""

    def __init__(self, vehicles: Dict[str, dict]):
        self.vehicles = vehicles

    def get(self, vehicle_id: str) -> Optional[dict]:
        return self.vehicles.get(vehicle_id)

    @classmethod
    def from_rows(cls, vehicle_rows, service_rows) -> "StaticContextProvider":
        services = defaultdict(lambda: defaultdict(list))
        for vid, comp, occurred_at, odo in service_rows:
            services[vid][comp].append((_ms(occurred_at), None if odo is None else float(odo)))
        vehicles = {}
        for vid, vtype, mfg, comps in vehicle_rows:
            vehicles[vid] = {
                "vehicle_type": vtype,
                "manufacture_date_ms": date_to_ms(mfg),
                "components": [c for c in (comps or []) if c],
                "services": {c: sorted(v) for c, v in services[vid].items()},
            }
        return cls(vehicles)

    @classmethod
    def load(cls, pg_conn) -> "StaticContextProvider":
        with pg_conn.cursor() as cur:
            cur.execute(VEHICLES_SQL)
            vrows = cur.fetchall()
            cur.execute(SERVICES_SQL)
            srows = cur.fetchall()
        return cls.from_rows(vrows, srows)


class PostgresContextCache:
    def __init__(self, conn_factory, refresh_seconds: float = 60.0):
        self.conn_factory = conn_factory
        self.refresh_seconds = refresh_seconds
        self._provider: Optional[StaticContextProvider] = None
        self._loaded_at = 0.0

    def _refresh_if_due(self):
        if self._provider is None or time.time() - self._loaded_at >= self.refresh_seconds:
            conn = self.conn_factory()
            try:
                self._provider = StaticContextProvider.load(conn)
            finally:
                conn.close()
            self._loaded_at = time.time()
            logger.info("Context cache refreshed: %d vehicles", len(self._provider.vehicles))

    def get(self, vehicle_id: str) -> Optional[dict]:
        self._refresh_if_due()
        ctx = self._provider.get(vehicle_id)
        if ctx is None and time.time() - self._loaded_at > 5.0:
            # Unknown vehicle: force one early refresh (newly seeded vehicles).
            self._loaded_at = 0.0
            self._refresh_if_due()
            ctx = self._provider.get(vehicle_id)
        return ctx
