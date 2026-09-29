"""
Kafka Producer Sink for Live Simulation Mode.
Publishes OEM-A formatted telemetry envelopes to oem.inbound topic keyed by VIN.
"""

import json
import logging
import time
from typing import Dict, Any

try:
    from kafka import KafkaProducer
except ImportError:
    KafkaProducer = None

logger = logging.getLogger("simulator.sink.kafka")


class KafkaSink:
    def __init__(self, bootstrap_servers: str, topic: str):
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self.producer = None

    def connect(self, max_retries: int = 30):
        if KafkaProducer is None:
            raise RuntimeError("kafka-python is not installed.")

        retries = 0
        while retries < max_retries:
            try:
                logger.info("Connecting KafkaSink to Kafka at %s...", self.bootstrap_servers)
                self.producer = KafkaProducer(
                    bootstrap_servers=self.bootstrap_servers.split(","),
                    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                    key_serializer=lambda k: k.encode("utf-8") if k else None,
                    # Durable publish: wait for the in-sync replicas and retry through broker restarts.
                    acks="all",
                    retries=20,
                    retry_backoff_ms=500,
                    max_in_flight_requests_per_connection=1,  # retries cannot reorder a vehicle's events
                    request_timeout_ms=15000,
                    linger_ms=10,
                )
                logger.info("KafkaSink connected successfully.")
                return
            except Exception as e:
                retries += 1
                logger.warning("Kafka not ready (%s). Retrying in 2s (%d/%d)...", e, retries, max_retries)
                time.sleep(2)
        raise TimeoutError(f"Failed to connect KafkaSink to {self.bootstrap_servers}")

    def send(self, vin: str, envelope: Dict[str, Any]):
        if not self.producer:
            raise RuntimeError("KafkaSink is not connected.")
        self.producer.send(self.topic, key=vin, value=envelope)

    def flush(self):
        if self.producer:
            self.producer.flush()

    def close(self):
        if self.producer:
            self.producer.close()
            logger.info("KafkaSink closed.")
