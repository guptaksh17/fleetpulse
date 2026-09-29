#!/usr/bin/env python3
"""
Generate Bulk Historical Telemetry and Ground Truth for FleetPulse.
CLI interface to run the offline simulator for ML dataset preparation.
"""

import argparse
import logging
import os
import sys

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulator.config import load_config
from simulator.runner_offline import run_offline_simulation
from simulator.sinks import TimescaleBulkSink, PostgresGroundTruthSink

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("generate-history")


def reset_history(timescale_conn_str: str, postgres_conn_str: str):
    """Deletes previously generated history (development data only) and reports what was removed."""
    import psycopg2

    with psycopg2.connect(timescale_conn_str) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM telemetry")
        n_tel = cur.fetchone()[0]
        cur.execute("TRUNCATE telemetry")
    with psycopg2.connect(postgres_conn_str) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM maintenance_event")
        n_evt = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM trip")
        n_trip = cur.fetchone()[0]
        cur.execute("TRUNCATE maintenance_event, trip")
    logger.info("Reset: deleted %d telemetry rows, %d maintenance events, %d trips", n_tel, n_evt, n_trip)


def main():
    parser = argparse.ArgumentParser(description="FleetPulse Bulk History Generator")
    parser.add_argument("--config", type=str, default="configs/offline_train.yaml", help="Path to config YAML")
    parser.add_argument("--days", type=float, default=None, help="Number of days to simulate (default from config or 90)")
    parser.add_argument("--vehicles", type=int, default=None, help="Override number of vehicles")
    parser.add_argument("--reset", action="store_true", help="Truncate telemetry, trip and maintenance_event before generating")
    parser.add_argument("--checkpoint-out", type=str, default="data/checkpoints/offline_checkpoint.json", help="Path to save checkpoint")

    # DB Connection params
    parser.add_argument("--timescale-host", type=str, default=os.getenv("TIMESCALE_HOST", "localhost"))
    parser.add_argument("--timescale-port", type=str, default=os.getenv("TIMESCALE_PORT", "5433"))
    parser.add_argument("--timescale-db", type=str, default=os.getenv("TIMESCALE_DB", "fleetpulse"))
    parser.add_argument("--timescale-user", type=str, default=os.getenv("TIMESCALE_USER", "fleetpulse"))
    parser.add_argument("--timescale-password", type=str, default=os.getenv("TIMESCALE_PASSWORD", "fleetpulse"))

    parser.add_argument("--postgres-host", type=str, default=os.getenv("POSTGRES_HOST", "localhost"))
    parser.add_argument("--postgres-port", type=str, default=os.getenv("POSTGRES_PORT", "5434"))
    parser.add_argument("--postgres-db", type=str, default=os.getenv("POSTGRES_DB", "fleetpulse"))
    parser.add_argument("--postgres-user", type=str, default=os.getenv("POSTGRES_USER", "fleetpulse"))
    parser.add_argument("--postgres-password", type=str, default=os.getenv("POSTGRES_PASSWORD", "fleetpulse"))

    args = parser.parse_args()

    config = load_config(args.config)

    timescale_conn_str = (
        f"host={args.timescale_host} port={args.timescale_port} "
        f"dbname={args.timescale_db} user={args.timescale_user} password={args.timescale_password}"
    )
    postgres_conn_str = (
        f"host={args.postgres_host} port={args.postgres_port} "
        f"dbname={args.postgres_db} user={args.postgres_user} password={args.postgres_password}"
    )

    if args.reset:
        reset_history(timescale_conn_str, postgres_conn_str)

    telemetry_sink = TimescaleBulkSink(timescale_conn_str, batch_size=5000)
    telemetry_sink.connect()
    ground_truth_sink = PostgresGroundTruthSink(postgres_conn_str)
    ground_truth_sink.connect()

    logger.info("Starting historical telemetry and ground truth generation...")
    stats = run_offline_simulation(
        config=config,
        telemetry_sink=telemetry_sink,
        ground_truth_sink=ground_truth_sink,
        days=args.days,
        vehicles_override=args.vehicles,
        checkpoint_out=args.checkpoint_out,
    )
    logger.info("Generation finished. Summary: %s", stats)


if __name__ == "__main__":
    main()
