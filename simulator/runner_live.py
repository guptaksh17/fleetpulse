"""
Live Real-Time Fleet Simulator for FleetPulse.
Publishes OEM-A envelopes to Kafka topic oem.inbound with accelerated clock pacing,
persists ground-truth maintenance events and trips to PostgreSQL, and supports
checkpoint resume. Uses the shared FleetEngine, so a live run resumed from an offline
checkpoint continues the exact same simulated history.
"""

import logging
import os
import random
import signal
import time
from typing import Optional

from .config import SimConfig
from .engine import FleetEngine
from .serializers import OEMAEnvelopeSerializer, OEMBEnvelopeSerializer
from .sinks import KafkaSink, PostgresGroundTruthSink

logger = logging.getLogger("simulator.runner.live")


def run_live_simulation(
    config: SimConfig,
    kafka_bootstrap: str,
    postgres_conn_str: str,
    checkpoint_in: Optional[str] = None,
    checkpoint_out: Optional[str] = None,
    speedup_override: Optional[float] = None,
    max_steps: Optional[int] = None,
    max_wall_seconds: Optional[float] = None,
    inject_dtc_code: Optional[str] = None,
    inject_dtc_vehicles: int = 1,
    inject_after_steps: int = 5,
    inject_duration_steps: int = 3,
    topic: str = "oem.inbound",
    duplicate_rate: float = 0.0,
):
    """
    Paces one simulation step per (step_seconds / speedup) wall seconds.
    speedup defaults to step_seconds / emit_interval_seconds from config.
    An injected DTC is added to the first `inject_duration_steps` payloads emitted by each of the
    first `inject_dtc_vehicles` vehicles once `inject_after_steps` steps of this run have passed.
    """
    engine = FleetEngine(config, legacy_mode=config.legacy)
    if checkpoint_in and os.path.exists(checkpoint_in):
        engine.load_checkpoint(checkpoint_in)

    step_seconds = engine.step_seconds
    speedup = speedup_override or (step_seconds / max(1e-6, config.clock.emit_interval_seconds))
    step_delay = step_seconds / speedup
    logger.info(
        "Live simulation: %d vehicles from %s, step %d s, speedup %.1fx (%.3f s wall per step)",
        len(engine.vehicles), engine.sim_time.isoformat(), step_seconds, speedup, step_delay,
    )

    kafka_sink = KafkaSink(kafka_bootstrap, topic=topic)
    kafka_sink.connect()
    pg_sink = PostgresGroundTruthSink(postgres_conn_str)
    pg_sink.connect()

    running = True

    def handle_shutdown(signum, frame):
        nonlocal running
        logger.info("Received signal %s, shutting down after the current step.", signum)
        running = False

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Separate RNG so duplicate injection never perturbs the simulation streams.
    dup_rng = random.Random(config.seed + 7919)
    duplicates = 0
    inject_left = {v.vehicle_id: inject_duration_steps for v in engine.vehicles[:inject_dtc_vehicles]} if inject_dtc_code else {}
    steps_done = 0
    sent = 0
    t_start = time.time()
    try:
        while running:
            loop_start = time.time()
            outputs = engine.step()
            events, trips = [], []
            for out in outputs:
                payload = out.payload
                if steps_done >= inject_after_steps and inject_left.get(out.vehicle.vehicle_id, 0) > 0:
                    inject_left[out.vehicle.vehicle_id] -= 1
                    payload = dict(payload)
                    payload["faultCodes"] = sorted(set(payload["faultCodes"]) | {inject_dtc_code})
                    if payload["event"] == "TELEMETRY":
                        payload["event"] = "DTC"
                serializer = OEMBEnvelopeSerializer if out.vehicle.oem_id == "OEM_B" else OEMAEnvelopeSerializer
                envelope = serializer.serialize(payload, oem_id=out.vehicle.oem_id)
                kafka_sink.send(out.vehicle.vin, envelope)
                sent += 1
                if duplicate_rate > 0 and dup_rng.random() < duplicate_rate:
                    kafka_sink.send(out.vehicle.vin, envelope)
                    sent += 1
                    duplicates += 1
                events.extend(out.events)
                if out.trip:
                    trips.append(out.trip)
            kafka_sink.flush()
            if events:
                pg_sink.save_maintenance_events(events)
            if trips:
                pg_sink.save_trips(trips)

            steps_done += 1
            if checkpoint_out and steps_done % 500 == 0:
                engine.save_checkpoint(checkpoint_out)
            if max_steps and steps_done >= max_steps:
                logger.info("Reached max_steps=%d.", max_steps)
                break
            if max_wall_seconds and time.time() - t_start >= max_wall_seconds:
                logger.info("Reached max_wall_seconds=%.0f.", max_wall_seconds)
                break
            remaining = step_delay - (time.time() - loop_start)
            if remaining > 0:
                time.sleep(remaining)
    finally:
        if checkpoint_out:
            engine.save_checkpoint(checkpoint_out)
        kafka_sink.close()
        pg_sink.close()
        logger.info(
            "Live simulation stopped at %s after %d steps, %d messages sent (%d injected duplicates).",
            engine.sim_time.isoformat(), steps_done, sent, duplicates,
        )
    return {"steps": steps_done, "messages_sent": sent, "duplicates": duplicates, "sim_time": engine.sim_time.isoformat()}
