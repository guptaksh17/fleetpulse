"""
Offline Batch History Generator for FleetPulse Simulator.
Simulates fleet operations over weeks/months with accelerated execution,
writing observable telemetry to a telemetry sink (TimescaleDB) and ground truth
(maintenance events, trips) to a ground-truth sink (PostgreSQL).
"""

from datetime import timedelta
import logging
import time
from typing import Optional

from .config import SimConfig
from .engine import FleetEngine

logger = logging.getLogger("simulator.runner.offline")


def run_offline_simulation(
    config: SimConfig,
    telemetry_sink,
    ground_truth_sink,
    days: Optional[int] = None,
    vehicles_override: Optional[int] = None,
    checkpoint_out: Optional[str] = None,
    progress_interval_days: int = 5,
) -> dict:
    """
    Runs the shared FleetEngine from config.clock.start_time for `days` simulated days.
    Sinks must provide add_telemetry(vehicle, payload), flush(), close(),
    save_maintenance_events(events) and save_trips(trips).
    Returns summary statistics.
    """
    if vehicles_override:
        config.population.vehicles = vehicles_override
    total_days = days if days is not None else config.clock.days

    engine = FleetEngine(config)
    end_time = engine.start_time + timedelta(days=total_days)

    logger.info(
        "Offline simulation: %d vehicles, %d days from %s, step %d s, heartbeat %d s, scale %.3f, seed %d, config %s",
        len(engine.vehicles), total_days, engine.start_time.isoformat(), engine.step_seconds,
        engine.heartbeat_seconds, config.degradation.scale, config.seed, config.config_hash[:8],
    )

    stats = {
        "vehicles": len(engine.vehicles),
        "days": total_days,
        "start_time": engine.start_time.isoformat(),
        "telemetry_rows": 0,
        "trips": 0,
        "maintenance_events": 0,
        "event_types": {"MAINTENANCE_REQUIRED": 0, "FAILURE": 0, "SERVICE_COMPLETED": 0},
        "sudden_failures": 0,
    }
    vehicles_with_event = set()
    components_with_event = set()
    progress_every = int(progress_interval_days * 86400 / engine.step_seconds)
    t0 = time.time()

    def on_step(sim_time, outputs):
        events, trips = [], []
        for out in outputs:
            telemetry_sink.add_telemetry(out.vehicle, out.payload)
            stats["telemetry_rows"] += 1
            for e in out.events:
                events.append(e)
                stats["maintenance_events"] += 1
                stats["event_types"][e.event_type] += 1
                if e.event_type in ("MAINTENANCE_REQUIRED", "FAILURE"):
                    vehicles_with_event.add(out.vehicle.vehicle_id)
                    components_with_event.add(e.vehicle_component_id)
                if e.event_type == "FAILURE" and e.metadata.get("failure_type") == "SUDDEN_FAILURE":
                    stats["sudden_failures"] += 1
            if out.trip:
                trips.append(out.trip)
                stats["trips"] += 1
        if events:
            ground_truth_sink.save_maintenance_events(events)
        if trips:
            ground_truth_sink.save_trips(trips)
        if engine.step_index % progress_every == 0:
            elapsed = time.time() - t0
            logger.info(
                "Progress %s | events %d | rows %d (%.0f rows/s)",
                engine.sim_time.strftime("%Y-%m-%d %H:%M"), stats["maintenance_events"],
                stats["telemetry_rows"], stats["telemetry_rows"] / max(0.1, elapsed),
            )

    try:
        engine.run(end_time, on_step)
        telemetry_sink.flush()
        if checkpoint_out:
            engine.save_checkpoint(checkpoint_out)
    finally:
        telemetry_sink.close()
        ground_truth_sink.close()

    n_components = sum(len(v.components) for v in engine.vehicles)
    total_maint = stats["event_types"]["MAINTENANCE_REQUIRED"] + stats["event_types"]["FAILURE"]
    stats["wall_seconds"] = round(time.time() - t0, 1)
    stats["end_time"] = engine.sim_time.isoformat()
    stats["vehicles_with_event_count"] = len(vehicles_with_event)
    stats["components_with_event_count"] = len(components_with_event)
    stats["active_components"] = n_components
    stats["vehicle_prevalence_pct"] = round(100.0 * len(vehicles_with_event) / max(1, len(engine.vehicles)), 2)
    stats["component_prevalence_pct"] = round(100.0 * len(components_with_event) / max(1, n_components), 2)
    stats["sudden_ratio_pct"] = round(100.0 * stats["sudden_failures"] / max(1, total_maint), 2)

    logger.info("Offline simulation finished: %s", stats)
    return stats
