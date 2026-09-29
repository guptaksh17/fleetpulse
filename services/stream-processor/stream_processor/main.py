"""
Stream Processing Service Main Consumer Loop (Phases 2 and 4).
Consumes canonical telemetry events from vehicle.normalized. Per batch:
  1. dedup read check (in-batch set, rotating Bloom, Redis)
  2. TimescaleDB sink write (ON CONFLICT DO NOTHING)
  3. feature engine: one Lua script per accepted event sets the dedup key (NX) and applies
     the bucket statistics atomically (pipelined); without features, plain mark_seen
  4. Bloom add
  5. snapshot step for every vehicle in the batch, duplicates included
  6. manual Kafka offset commit
"""

import json
import logging
import os
import sys
import time
from typing import List

try:
    from kafka import KafkaConsumer
    from kafka.consumer.subscription_state import ConsumerRebalanceListener
except ImportError:
    KafkaConsumer = None
    ConsumerRebalanceListener = object

try:
    import redis
except ImportError:
    redis = None

import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from stream_processor.dedup.stage import DedupStage
from stream_processor.pipeline import process_batch
from stream_processor.sinks.timescale_sink import TimescaleSink

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("stream-processor")


class MetricsServer:
    def __init__(self, dedup_stage: DedupStage, port: int = 8080, feature_engine=None):
        self.dedup_stage = dedup_stage
        self.feature_engine = feature_engine
        self.port = port
        self.server = None

    def start(self):
        stage = self.dedup_stage
        fe = self.feature_engine

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/metrics":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    metrics = stage.get_and_reset_counters()
                    metrics["bloom_trusted"] = stage.bloom_trusted()
                    if fe is not None:
                        metrics["features"] = fe.metrics()
                    self.wfile.write(json.dumps(metrics).encode("utf-8"))
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):
                pass

        try:
            self.server = HTTPServer(("0.0.0.0", self.port), Handler)  # nosec B104
            t = threading.Thread(target=self.server.serve_forever, daemon=True)
            t.start()
            logger.info("Metrics HTTP server listening on port %d (/metrics)", self.port)
        except Exception as e:
            logger.warning("Could not start metrics server: %s", e)


class RebalanceHandler(ConsumerRebalanceListener):
    def __init__(self, dedup_stage: DedupStage):
        self.dedup_stage = dedup_stage

    def on_partitions_revoked(self, revoked):
        logger.info("Kafka partitions revoked: %s", revoked)

    def on_partitions_assigned(self, assigned):
        logger.info("Kafka partitions assigned: %s", assigned)
        # Reset cold-start guard on any rebalance per specification
        self.dedup_stage.reset_cold_start()


def connect_redis(redis_host: str, redis_port: int, max_retries: int = 30):
    if redis is None:
        raise RuntimeError("redis package is not installed.")

    retries = 0
    while retries < max_retries:
        try:
            logger.info("Connecting to Redis at %s:%d...", redis_host, redis_port)
            r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
            r.ping()
            logger.info("Connected to Redis successfully.")
            return r
        except Exception as e:
            retries += 1
            logger.warning("Redis not ready (%s). Retrying in 2s (%d/%d)...", e, retries, max_retries)
            time.sleep(2)
    raise TimeoutError(f"Could not connect to Redis at {redis_host}:{redis_port}")


def connect_kafka_consumer(bootstrap_servers: str, topic: str, group_id: str, rebalance_listener, max_retries: int = 30):
    if KafkaConsumer is None:
        raise RuntimeError("kafka-python library is not installed.")

    retries = 0
    while retries < max_retries:
        try:
            logger.info("Connecting Kafka consumer to %s (topic: %s, group: %s)...", bootstrap_servers, topic, group_id)
            consumer = KafkaConsumer(
                bootstrap_servers=bootstrap_servers.split(","),
                group_id=group_id,
                auto_offset_reset="earliest",
                enable_auto_commit=False,  # Strict manual offset commits
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                key_deserializer=lambda k: k.decode("utf-8") if k else None,
            )
            consumer.subscribe([topic], listener=rebalance_listener)
            logger.info("Kafka consumer subscribed successfully to %s.", topic)
            return consumer
        except Exception as e:
            retries += 1
            logger.warning("Kafka consumer connect failed (%s). Retrying in 2s (%d/%d)...", e, retries, max_retries)
            time.sleep(2)
    raise TimeoutError(f"Failed to connect Kafka consumer to {bootstrap_servers}")


def build_feature_engine(redis_client, dedup_window_s: int):
    """Creates the Phase 4 feature engine (Postgres context cache + component_features sink)."""
    import psycopg2
    from fleetpulse_features.config import load_feature_config
    from stream_processor.features.context_cache import PostgresContextCache
    from stream_processor.features.engine import FeatureEngine
    from stream_processor.features.sink import ComponentFeatureSink

    pg_conn_str = (
        f"host={os.getenv('POSTGRES_HOST', 'localhost')} port={os.getenv('POSTGRES_PORT', '5432')} "
        f"dbname={os.getenv('POSTGRES_DB', 'fleetpulse')} user={os.getenv('POSTGRES_USER', 'fleetpulse')} "
        f"password={os.getenv('POSTGRES_PASSWORD', 'fleetpulse')}"
    )
    ts_conn_str = (
        f"host={os.getenv('TIMESCALE_HOST', 'localhost')} port={os.getenv('TIMESCALE_PORT', '5432')} "
        f"dbname={os.getenv('TIMESCALE_DB', 'fleetpulse')} user={os.getenv('TIMESCALE_USER', 'fleetpulse')} "
        f"password={os.getenv('TIMESCALE_PASSWORD', 'fleetpulse')}"
    )
    fcfg = load_feature_config()
    context = PostgresContextCache(lambda: psycopg2.connect(pg_conn_str), refresh_seconds=float(os.getenv("CONTEXT_REFRESH_SECONDS", "60")))
    sink = ComponentFeatureSink(lambda: psycopg2.connect(ts_conn_str))
    engine = FeatureEngine(redis_client, fcfg, context, sink.write, dedup_ttl_seconds=dedup_window_s)
    logger.info("Feature engine enabled (schema %s, windows %s)", fcfg.schema_version, [w.name for w in fcfg.windows])
    return engine


def main():
    bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    normalized_topic = os.getenv("KAFKA_NORMALIZED_TOPIC", "vehicle.normalized")
    group_id = os.getenv("KAFKA_GROUP_ID", "stream-processor")

    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    dedup_window_s = int(os.getenv("DEDUP_WINDOW_SECONDS", "900"))
    bloom_capacity = int(os.getenv("BLOOM_CAPACITY", "1000000"))
    bloom_fp_rate = float(os.getenv("BLOOM_FP_RATE", "0.01"))

    ts_host = os.getenv("TIMESCALE_HOST", "localhost")
    ts_port = os.getenv("TIMESCALE_PORT", "5432")
    ts_db = os.getenv("TIMESCALE_DB", "fleetpulse")
    ts_user = os.getenv("TIMESCALE_USER", "fleetpulse")
    ts_password = os.getenv("TIMESCALE_PASSWORD", "fleetpulse")
    db_conn_str = f"host={ts_host} port={ts_port} dbname={ts_db} user={ts_user} password={ts_password}"

    batch_size = int(os.getenv("BATCH_SIZE", "500"))
    poll_timeout_ms = int(os.getenv("POLL_TIMEOUT_MS", "200"))

    logger.info("Initializing Stream Processing Service (Phase 4, features %s)...", os.getenv("FEATURES_ENABLED", "true"))
    logger.info(
        "Dedup Config: Window=%ds, Bloom Capacity=%d, FP Rate=%.3f",
        dedup_window_s,
        bloom_capacity,
        bloom_fp_rate,
    )

    redis_client = connect_redis(redis_host, redis_port)
    dedup_stage = DedupStage(
        redis_client=redis_client,
        window_seconds=dedup_window_s,
        bloom_capacity=bloom_capacity,
        bloom_fp_rate=bloom_fp_rate,
    )

    features_enabled = os.getenv("FEATURES_ENABLED", "true").lower() == "true"
    feature_engine = build_feature_engine(redis_client, dedup_window_s) if features_enabled else None
    from stream_processor.features.engine import flatten_canonical_event

    metrics_server = MetricsServer(dedup_stage, port=8080, feature_engine=feature_engine)
    metrics_server.start()

    sink = TimescaleSink(db_conn_str)
    sink.connect()

    rebalance_listener = RebalanceHandler(dedup_stage)
    consumer = connect_kafka_consumer(bootstrap_servers, normalized_topic, group_id, rebalance_listener)

    last_metric_log = time.time()
    logger.info("Stream Processor consumer loop active. Waiting for normalized telemetry...")

    try:
        while True:
            # Poll batch of records
            raw_batches = consumer.poll(timeout_ms=poll_timeout_ms, max_records=batch_size)

            messages = []
            for tp, records in raw_batches.items():
                for record in records:
                    messages.append(record)

            events = [m.value for m in messages if isinstance(m.value, dict)]
            if events:
                # Steps 1-5 (dedup check, sink, Lua mark+features, Bloom, snapshots)
                process_batch(
                    events,
                    dedup_stage,
                    sink.write_batch,
                    feature_engine=feature_engine,
                    to_record=flatten_canonical_event if feature_engine is not None else None,
                )
            if messages:
                # Step 6: commit offsets only after every downstream effect succeeded
                consumer.commit()

            # Log deduplication metrics every 10 seconds
            now = time.time()
            if now - last_metric_log >= 10.0:
                metrics = dedup_stage.get_and_reset_counters()
                logger.info(
                    "DEDUP STATS (10s): accepted=%d, duplicates_dropped=%d, bloom_fast_path=%d, "
                    "redis_reads=%d, bloom_untrusted_checks=%d, bloom_trusted=%s",
                    metrics["accepted"],
                    metrics["duplicates_dropped"],
                    metrics["bloom_fast_path"],
                    metrics["redis_reads"],
                    metrics["bloom_untrusted_checks"],
                    dedup_stage.bloom_trusted(),
                )
                if feature_engine is not None:
                    fm = feature_engine.metrics()
                    logger.info(
                        "FEATURE STATS (cumulative): events_applied=%d, duplicates_skipped=%d, late_events_ignored=%d, "
                        "snapshots_written=%d, snapshot_latency_ms p50=%s p95=%s",
                        fm.get("events_applied", 0), fm.get("duplicates_skipped", 0), fm.get("late_events_ignored", 0),
                        fm.get("snapshots_written", 0), fm.get("snapshot_latency_ms_p50"), fm.get("snapshot_latency_ms_p95"),
                    )
                last_metric_log = now

    except KeyboardInterrupt:
        logger.info("Stream Processor interrupted. Exiting.")
    finally:
        consumer.close()
        if sink.conn:
            sink.conn.close()
        logger.info("Stream Processor shutdown complete.")


if __name__ == "__main__":
    main()
