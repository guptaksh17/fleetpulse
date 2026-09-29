#!/usr/bin/env python3
"""
FleetPulse Identity Resolver Service (Phase 2)
Consumes raw OEM envelopes from Kafka topic: oem.inbound
Resolves VIN -> vehicle_id via Postgres vehicle table.
Publishes enriched envelope (with vehicle_id & vehicle_type) to Kafka topic: vehicle.raw.
Manual offset commit after successful publish.
"""

import json
import re
import logging
import os
import sys
import time

try:
    from kafka import KafkaConsumer, KafkaProducer
except ImportError:
    KafkaConsumer = None
    KafkaProducer = None

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    DB_OPERATIONAL_ERROR = (psycopg2.OperationalError,)
except ImportError:
    psycopg2 = None
    RealDictCursor = None
    DB_OPERATIONAL_ERROR = ()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("identity-resolver")


class IdentityResolver:
    def __init__(self, pg_conn_str: str):
        self.pg_conn_str = pg_conn_str
        self.pg_conn = None
        self._cache = {}  # vin -> {"vehicle_id": ..., "vehicle_type": ...}

    def connect_db(self, max_retries: int = 30):
        if psycopg2 is None:
            raise RuntimeError("psycopg2 is not installed.")

        retries = 0
        while retries < max_retries:
            try:
                logger.info("Attempting connection to Postgres database...")
                self.pg_conn = psycopg2.connect(self.pg_conn_str)
                self.pg_conn.autocommit = True
                logger.info("Connected to Postgres successfully.")
                return
            except Exception as e:
                retries += 1
                logger.warning(
                    "Postgres not ready (%s). Retrying in 2s (%d/%d)...",
                    e,
                    retries,
                    max_retries,
                )
                time.sleep(2)
        raise TimeoutError("Could not connect to Postgres database after max retries")

    def resolve_vin(self, vin: str) -> dict:
        if vin in self._cache:
            return self._cache[vin]

        if not self.pg_conn or self.pg_conn.closed:
            self.connect_db()

        cursor_kwargs = {"cursor_factory": RealDictCursor} if RealDictCursor else {}
        try:
            with self.pg_conn.cursor(**cursor_kwargs) as cur:
                cur.execute(
                    "SELECT vehicle_id, vehicle_type, oem_id FROM vehicle WHERE vin = %s;",
                    (vin,),
                )
                row = cur.fetchone()
                if not row:
                    logger.critical(
                        "FATAL: VIN '%s' not found in Postgres vehicle table! "
                        "Phase 2 requires seed data to contain all active simulation vehicles.",
                        vin,
                    )
                    raise ValueError(f"Unknown VIN: {vin}. Aborting per Phase 2 fail-loud specification.")

                if isinstance(row, dict):
                    vehicle_id = row["vehicle_id"]
                    vehicle_type = row["vehicle_type"]
                    oem_id = row["oem_id"]
                else:
                    vehicle_id, vehicle_type, oem_id = row[0], row[1], row[2]

                vehicle_info = {
                    "vehicle_id": str(vehicle_id),
                    "vehicle_type": str(vehicle_type),
                    "oem_id": str(oem_id),
                }
                self._cache[vin] = vehicle_info
                logger.info(
                    "Resolved VIN %s -> internal vehicle_id %s (type=%s)",
                    vin,
                    vehicle_info["vehicle_id"],
                    vehicle_info["vehicle_type"],
                )
                return vehicle_info
        except DB_OPERATIONAL_ERROR as e:
            logger.warning("Database connection error (%s). Reconnecting...", e)
            self.connect_db()
            return self.resolve_vin(vin)


VIN_TRANSLIT = {**{str(d): d for d in range(10)}, "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
                "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9, "S": 2, "T": 3, "U": 4, "V": 5, "W": 6,
                "X": 7, "Y": 8, "Z": 9}
VIN_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)
VIN_PATTERN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")  # 17 characters, never I, O or Q


def is_valid_vin(vin) -> bool:
    """ISO 3779 structure and North American check digit (position 9). O(17) time, O(1) space."""
    if not isinstance(vin, str) or not VIN_PATTERN.match(vin):
        return False
    total = sum(VIN_TRANSLIT[c] * w for c, w in zip(vin, VIN_WEIGHTS))
    check = total % 11
    return vin[8] == ("X" if check == 10 else str(check))


def send_to_dlq(producer, dlq_topic: str, envelope, reason: str, source_topic: str):
    """Poison or unresolvable messages go to the dead-letter topic with a reason instead of crash-looping."""
    producer.send(dlq_topic, key=None, value={"reason": reason, "source_topic": source_topic, "envelope": envelope,
                                             "failed_at": time.time()})
    producer.flush()


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
                enable_auto_commit=False,  # Disabled auto-commit per A3
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                key_deserializer=lambda k: k.decode("utf-8") if k else None,
            )
            logger.info("Kafka consumer connected successfully (auto-commit disabled).")
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


def main():
    bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    inbound_topic = os.getenv("KAFKA_INBOUND_TOPIC", "oem.inbound")
    outbound_topic = os.getenv("KAFKA_OUTBOUND_TOPIC", "vehicle.raw")
    group_id = os.getenv("KAFKA_GROUP_ID", "identity-resolver-group")

    if inbound_topic == outbound_topic:
        raise ValueError(f"Topology invariant violation: inbound topic '{inbound_topic}' must not equal outbound topic '{outbound_topic}'")

    pg_host = os.getenv("POSTGRES_HOST", "localhost")
    pg_port = os.getenv("POSTGRES_PORT", "5432")
    pg_db = os.getenv("POSTGRES_DB", "fleetpulse")
    pg_user = os.getenv("POSTGRES_USER", "fleetpulse")
    pg_password = os.getenv("POSTGRES_PASSWORD", "fleetpulse")

    pg_conn_str = f"host={pg_host} port={pg_port} dbname={pg_db} user={pg_user} password={pg_password}"

    logger.info("Initializing Identity Resolver Service (Phase 2)...")
    logger.info("Topology: Consuming from '%s' -> Publishing to '%s'", inbound_topic, outbound_topic)

    resolver = IdentityResolver(pg_conn_str)
    resolver.connect_db()

    consumer = connect_kafka_consumer(bootstrap_servers, inbound_topic, group_id)
    producer = connect_kafka_producer(bootstrap_servers)

    dlq_topic = os.getenv("KAFKA_DLQ_TOPIC", "vehicle.dlq")
    logger.info("Identity Resolver ready and listening on %s (dead letters -> %s)...", inbound_topic, dlq_topic)

    try:
        for message in consumer:
            envelope = message.value
            if not isinstance(envelope, dict):
                logger.warning("Received invalid non-dict envelope: %s", envelope)
                consumer.commit()
                continue

            payload = envelope.get("payload", {})
            # OEM-A carries payload.vin; OEM-B nests it under payload.vehicle.vin.
            vin = payload.get("vin") or (payload.get("vehicle") or {}).get("vin")
            if not vin:
                logger.error("Envelope missing VIN in payload: %s", envelope)
                send_to_dlq(producer, dlq_topic, envelope, "MISSING_VIN", inbound_topic)
                consumer.commit()
                continue

            if not is_valid_vin(vin):
                logger.error("Invalid VIN %r (pattern or check digit). Routed to %s.", vin, dlq_topic)
                send_to_dlq(producer, dlq_topic, envelope, "INVALID_VIN", inbound_topic)
                consumer.commit()
                continue

            # Lookup VIN in Postgres
            try:
                vehicle_info = resolver.resolve_vin(vin)
            except ValueError:
                send_to_dlq(producer, dlq_topic, envelope, "UNKNOWN_VIN", inbound_topic)
                consumer.commit()
                continue

            # Enrich raw envelope with internal vehicle_id and vehicle_type
            envelope["vehicle_id"] = vehicle_info["vehicle_id"]
            envelope["vehicle_type"] = vehicle_info["vehicle_type"]

            # Publish enriched envelope to vehicle.raw
            producer.send(outbound_topic, key=vin, value=envelope)
            producer.flush()

            # Commit offset only after downstream publish succeeds
            consumer.commit()

            logger.info(
                "Resolved and forwarded VIN: %s | vehicle_id: %s | seq: %s -> %s",
                vin,
                envelope["vehicle_id"],
                payload.get("sequence"),
                outbound_topic,
            )
    except KeyboardInterrupt:
        logger.info("Identity Resolver interrupted. Exiting.")
    finally:
        consumer.close()
        producer.close()
        logger.info("Identity Resolver shutdown complete.")


if __name__ == "__main__":
    main()
