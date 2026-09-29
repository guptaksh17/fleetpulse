"""Idempotent writer for the component_features hypertable."""

from datetime import datetime, timezone
import json
import logging
from typing import List, Tuple

try:
    from psycopg2.extras import execute_values
except ImportError:  # pragma: no cover
    execute_values = None

from .engine import to_jsonable

logger = logging.getLogger("stream-processor.features.sink")


class ComponentFeatureSink:
    def __init__(self, conn_factory, table: str = "component_features"):
        self.conn_factory = conn_factory
        self.table = table
        self.conn = None

    def _connection(self):
        if self.conn is None or self.conn.closed:
            self.conn = self.conn_factory()
            self.conn.autocommit = True
        return self.conn

    def write(self, rows: List[Tuple]):
        if not rows:
            return
        records = [
            (
                vid,
                comp,
                datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc),
                schema,
                bool(complete),
                json.dumps(to_jsonable(feats), sort_keys=True),
            )
            for vid, comp, t_ms, schema, complete, feats in rows
        ]
        # The table name is a constructor constant, never user input.
        sql = (
            f"INSERT INTO {self.table} (vehicle_id, component, feature_ts, feature_schema_version, window_complete, features) "  # nosec B608
            "VALUES %s ON CONFLICT (vehicle_id, component, feature_ts) DO NOTHING"
        )
        with self._connection().cursor() as cur:
            execute_values(cur, sql, records, page_size=1000)
