"""
PostgreSQL Storage Layer for Rule Engine.
Handles vehicle component lookup, alert creation, and audit logging in a single atomic transaction.
"""

import json
import logging
import time
import uuid
from typing import Dict, Optional, Tuple

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    DB_OPERATIONAL_ERROR = (psycopg2.OperationalError,)
except ImportError:
    psycopg2 = None
    RealDictCursor = None
    DB_OPERATIONAL_ERROR = ()

logger = logging.getLogger("rule-engine.storage")

INSERT_ALERT_SQL = """
INSERT INTO alert (
    alert_id,
    vehicle_component_id,
    alert_type,
    severity,
    source,
    risk_probability,
    message,
    created_at,
    status
) VALUES (
    %(alert_id)s,
    %(vehicle_component_id)s,
    %(alert_type)s,
    %(severity)s,
    'RULE',
    NULL,
    %(message)s,
    now(),
    'ACTIVE'
)
ON CONFLICT (vehicle_component_id, alert_type) WHERE status = 'ACTIVE'
DO NOTHING
RETURNING alert_id, created_at;
"""

INSERT_AUDIT_LOG_SQL = """
INSERT INTO audit_log (
    tenant_id,
    actor_type,
    actor_id,
    action,
    entity_type,
    entity_id,
    metadata,
    occurred_at
) VALUES (
    %(tenant_id)s,
    'SERVICE',
    'rule-engine',
    'ALERT_CREATED',
    'alert',
    %(entity_id)s,
    %(metadata)s,
    now()
);
"""


class RuleEngineStorage:
    def __init__(self, db_conn_str: str):
        self.db_conn_str = db_conn_str
        self.conn = None
        self._component_cache: Dict[Tuple[str, str], Optional[Tuple[str, str]]] = {}  # (vid, comp) -> (vc_id, status)
        self._tenant_cache: Dict[str, Optional[str]] = {}  # vid -> tenant_id
        self.incompatible_component_count = 0

    def connect(self, max_retries: int = 30):
        if psycopg2 is None:
            raise RuntimeError("psycopg2 is not installed.")

        retries = 0
        while retries < max_retries:
            try:
                logger.info("Connecting RuleEngineStorage to Postgres...")
                self.conn = psycopg2.connect(self.db_conn_str)
                logger.info("RuleEngineStorage connected successfully.")
                return
            except Exception as e:
                retries += 1
                logger.warning("Postgres not ready (%s). Retrying in 2s (%d/%d)...", e, retries, max_retries)
                time.sleep(2)
        raise TimeoutError("Could not connect to Postgres database after max retries")

    def get_vehicle_component(self, vehicle_id: str, component: str) -> Optional[str]:
        cache_key = (vehicle_id, component)
        if cache_key in self._component_cache:
            res = self._component_cache[cache_key]
            return res[0] if (res and res[1] == "ACTIVE") else None

        if not self.conn or self.conn.closed:
            self.connect()

        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT vehicle_component_id, status FROM vehicle_component WHERE vehicle_id = %s AND component = %s;",
                    (vehicle_id, component),
                )
                row = cur.fetchone()
                if not row:
                    logger.warning("No vehicle_component row for vehicle %s, component %s", vehicle_id, component)
                    self._component_cache[cache_key] = None
                    self.incompatible_component_count += 1
                    return None

                vc_id, status = str(row[0]), str(row[1])
                self._component_cache[cache_key] = (vc_id, status)
                if status != "ACTIVE":
                    logger.warning("Vehicle component (%s, %s) is %s (not ACTIVE). Skipping alert.", vehicle_id, component, status)
                    self.incompatible_component_count += 1
                    return None
                return vc_id
        except DB_OPERATIONAL_ERROR as e:
            logger.warning("DB error during component lookup (%s). Reconnecting...", e)
            self.connect()
            return self.get_vehicle_component(vehicle_id, component)

    def get_tenant_id(self, vehicle_id: str) -> Optional[str]:
        if vehicle_id in self._tenant_cache:
            return self._tenant_cache[vehicle_id]

        if not self.conn or self.conn.closed:
            self.connect()

        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT f.tenant_id 
                    FROM vehicle v
                    JOIN fleet f ON v.fleet_id = f.fleet_id
                    WHERE v.vehicle_id = %s;
                    """,
                    (vehicle_id,),
                )
                row = cur.fetchone()
                tenant_id = str(row[0]) if row else None
                self._tenant_cache[vehicle_id] = tenant_id
                return tenant_id
        except DB_OPERATIONAL_ERROR as e:
            logger.warning("DB error during tenant lookup (%s). Reconnecting...", e)
            self.connect()
            return self.get_tenant_id(vehicle_id)

    def persist_alert_transaction(
        self,
        vehicle_id: str,
        vehicle_component_id: str,
        component: str,
        alert_type: str,
        severity: str,
        message: str,
        dtc_code: str,
        trigger_event_id: str,
        trigger_event_ts: str,
    ) -> Optional[dict]:
        """
        Executes single atomic transaction:
          1. INSERT INTO alert ... ON CONFLICT DO NOTHING RETURNING alert_id
          2. If inserted: INSERT INTO audit_log
          3. Commit transaction
        Returns alert event dict if a new active alert was created, None if duplicate (conflict).
        """
        if not self.conn or self.conn.closed:
            self.connect()

        new_alert_id = str(uuid.uuid4())
        tenant_id = self.get_tenant_id(vehicle_id)

        record = {
            "alert_id": new_alert_id,
            "vehicle_component_id": vehicle_component_id,
            "alert_type": alert_type,
            "severity": severity,
            "message": message,
        }

        metadata_json = json.dumps({
            "dtc_code": dtc_code,
            "severity": severity,
            "trigger_event_id": trigger_event_id,
            "trigger_event_ts": trigger_event_ts,
        })

        try:
            with self.conn:
                with self.conn.cursor() as cur:
                    cur.execute(INSERT_ALERT_SQL, record)
                    ret = cur.fetchone()
                    if not ret:
                        # Conflict: active alert of this type already exists for this component
                        return None

                    created_alert_id, created_at = str(ret[0]), ret[1]

                    audit_record = {
                        "tenant_id": tenant_id,
                        "entity_id": created_alert_id,
                        "metadata": metadata_json,
                    }
                    cur.execute(INSERT_AUDIT_LOG_SQL, audit_record)

            created_at_iso = (
                created_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"
                if hasattr(created_at, "strftime")
                else str(created_at)
            )

            return {
                "alert_id": created_alert_id,
                "vehicle_id": vehicle_id,
                "vehicle_component_id": vehicle_component_id,
                "component": component,
                "alert_type": alert_type,
                "severity": severity,
                "source": "RULE",
                "message": message,
                "dtc_code": dtc_code,
                "created_at": created_at_iso,
                "trigger_event_id": trigger_event_id,
                "trigger_event_ts": trigger_event_ts,
            }
        except DB_OPERATIONAL_ERROR as e:
            logger.warning("DB error in persist_alert_transaction (%s). Reconnecting...", e)
            self.connect()
            raise
