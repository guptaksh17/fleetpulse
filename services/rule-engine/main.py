"""
FleetPulse Rule Engine Service (Phase 2).
Consumes canonical vehicle events from vehicle.normalized.
Evaluates DTC rules, persists alerts and audit logs to Postgres in atomic transactions,
and publishes alert events to Kafka topic: vehicle.alerts.
Logs latency (now - event.ingested_at) and commits Kafka offsets.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

try:
    from kafka import KafkaConsumer, KafkaProducer
except ImportError:
    KafkaConsumer = None
    KafkaProducer = None

from rules.dtc_rule import DtcRule
from storage.postgres import RuleEngineStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("rule-engine")


def connect_kafka_consumer(bootstrap_servers: str, topic: str, group_id: str, max_retries: int = 30) -> KafkaConsumer:
    if KafkaConsumer is None:
        raise RuntimeError("kafka-python library is not installed.")

    retries = 0
    while retries < max_retries:
        try:
            logger.info("Connecting Kafka consumer to %s (topic: %s, group: %s)...", bootstrap_servers, topic, group_id)
            consumer = KafkaConsumer(
                topic,
                bootstrap_servers=bootstrap_servers.split(","),
                group_id=group_id,
                auto_offset_reset="earliest",
                enable_auto_commit=False,  # Strict manual offset commits
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                key_deserializer=lambda k: k.decode("utf-8") if k else None,
            )
            logger.info("Kafka consumer connected successfully.")
            return consumer
        except Exception as e:
            retries += 1
            logger.warning("Kafka consumer connect failed (%s). Retrying in 2s (%d/%d)...", e, retries, max_retries)
            time.sleep(2)
    raise TimeoutError(f"Failed to connect Kafka consumer to {bootstrap_servers}")


def connect_kafka_producer(bootstrap_servers: str, max_retries: int = 30) -> KafkaProducer:
    if KafkaProducer is None:
        raise RuntimeError("kafka-python library is not installed.")

    retries = 0
    while retries < max_retries:
        try:
            logger.info("Connecting Kafka producer to %s...", bootstrap_servers)
            producer = KafkaProducer(
                bootstrap_servers=bootstrap_servers.split(","),
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
                retries=5,
                acks="all",
            )
            logger.info("Kafka producer connected successfully.")
            return producer
        except Exception as e:
            retries += 1
            logger.warning("Kafka producer connect failed (%s). Retrying in 2s (%d/%d)...", e, retries, max_retries)
            time.sleep(2)
    raise TimeoutError(f"Failed to connect Kafka producer to {bootstrap_servers}")


def calculate_latency_ms(ingested_at_str: str) -> float:
    try:
        # Standardize ISO 8601 parsing
        ts = ingested_at_str.replace("Z", "+00:00")
        ingested_dt = datetime.fromisoformat(ts)
        now_dt = datetime.now(timezone.utc)
        return max(0.0, (now_dt - ingested_dt).total_seconds() * 1000.0)
    except Exception:
        return 0.0


def main():
    bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    normalized_topic = os.getenv("KAFKA_NORMALIZED_TOPIC", "vehicle.normalized")
    alerts_topic = os.getenv("KAFKA_ALERTS_TOPIC", "vehicle.alerts")
    group_id = os.getenv("KAFKA_GROUP_ID", "rule-engine")
    dtc_registry_path = os.getenv("DTC_REGISTRY_PATH", "/contracts/dtc/dtc_registry.yaml")

    pg_host = os.getenv("POSTGRES_HOST", "localhost")
    pg_port = os.getenv("POSTGRES_PORT", "5432")
    pg_db = os.getenv("POSTGRES_DB", "fleetpulse")
    pg_user = os.getenv("POSTGRES_USER", "fleetpulse")
    pg_password = os.getenv("POSTGRES_PASSWORD", "fleetpulse")
    db_conn_str = f"host={pg_host} port={pg_port} dbname={pg_db} user={pg_user} password={pg_password}"

    logger.info("Initializing Rule Engine Service (Phase 2)...")
    rule = DtcRule(dtc_registry_path)
    storage = RuleEngineStorage(db_conn_str)
    storage.connect()

    consumer = connect_kafka_consumer(bootstrap_servers, normalized_topic, group_id)
    producer = connect_kafka_producer(bootstrap_servers)

    logger.info("Rule Engine listening on '%s', alerts published to '%s'", normalized_topic, alerts_topic)

    try:
        for message in consumer:
            event = message.value
            if not isinstance(event, dict):
                consumer.commit()
                continue

            hits = rule.evaluate(event)
            vehicle_id = event["vehicle_id"]
            event_id = event["event_id"]
            event_ts = event["event_ts"]
            ingested_at = event.get("ingested_at", "")

            for hit in hits:
                # 1. Resolve vehicle_component_id
                vc_id = storage.get_vehicle_component(vehicle_id, hit.component)
                if not vc_id:
                    continue

                alert_type = f"DTC_{hit.dtc_code}"

                # 2. Atomic insert alert + audit_log in DB
                alert_event = storage.persist_alert_transaction(
                    vehicle_id=vehicle_id,
                    vehicle_component_id=vc_id,
                    component=hit.component,
                    alert_type=alert_type,
                    severity=hit.severity,
                    message=hit.description,
                    dtc_code=hit.dtc_code,
                    trigger_event_id=event_id,
                    trigger_event_ts=event_ts,
                )

                # 3. Only if a new alert was created (not conflict), publish to vehicle.alerts
                if alert_event:
                    producer.send(alerts_topic, key=vehicle_id, value=alert_event)
                    producer.flush()

                    latency_ms = calculate_latency_ms(ingested_at)
                    logger.info(
                        "ALERT GENERATED | alert_id: %s | type: %s | component: %s | severity: %s | latency: %.2f ms",
                        alert_event["alert_id"],
                        alert_type,
                        hit.component,
                        hit.severity,
                        latency_ms,
                    )
                else:
                    logger.info("Duplicate active alert suppressed for component: %s, type: %s", hit.component, alert_type)

            # 4. Commit offset after all effects are persisted
            consumer.commit()

    except KeyboardInterrupt:
        logger.info("Rule Engine interrupted. Exiting.")
    finally:
        consumer.close()
        producer.close()
        if storage.conn:
            storage.conn.close()
        logger.info("Rule Engine shutdown complete.")


if __name__ == "__main__":
    main()
