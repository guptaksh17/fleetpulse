"""Connection pools for PostgreSQL (business state) and TimescaleDB (telemetry), and Redis."""

import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool
import redis


def _dsn(prefix: str, default_host: str, default_port: str) -> str:
    return (f"host={os.getenv(prefix + '_HOST', default_host)} port={os.getenv(prefix + '_PORT', default_port)} "
            f"dbname={os.getenv(prefix + '_DB', 'fleetpulse')} user={os.getenv(prefix + '_USER', 'fleetpulse')} "
            f"password={os.getenv(prefix + '_PASSWORD', 'fleetpulse')}")


_pg = None
_ts = None
_redis = None


def pg_pool():
    global _pg
    if _pg is None:
        _pg = psycopg2.pool.ThreadedConnectionPool(2, 20, _dsn("POSTGRES", "localhost", "5434"))
    return _pg


def ts_pool():
    global _ts
    if _ts is None:
        _ts = psycopg2.pool.ThreadedConnectionPool(1, 10, _dsn("TIMESCALE", "localhost", "5433"))
    return _ts


def redis_client():
    global _redis
    if _redis is None:
        _redis = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=int(os.getenv("REDIS_PORT", "6379")), decode_responses=True)
    return _redis


@contextmanager
def cursor(pool_fn=pg_pool, commit: bool = False):
    pool = pool_fn()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        if commit:
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)
