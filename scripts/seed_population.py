#!/usr/bin/env python3
"""
Seed Fleet Population into PostgreSQL.
Idempotently creates tenants, fleets, drivers, vehicles, and vehicle_components.
"""

import argparse
import logging
import os
import sys

import psycopg2
from psycopg2.extras import execute_values

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import timedelta

from simulator.config import load_config, SimulationRNG
from simulator.engine import parse_utc
from simulator.population import FleetPopulation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("seed-population")


def seed_database(config_path: str, pg_conn_str: str):
    logger.info("Loading config from %s...", config_path)
    config = load_config(config_path)

    rng_streams = SimulationRNG(config.seed)
    vehicles = FleetPopulation.generate_population(
        config=config,
        rng_population=rng_streams.population,
        legacy_mode=False,
    )
    logger.info("Generated population of %d vehicles.", len(vehicles))
    sim_start = parse_utc(config.clock.start_time)

    conn = psycopg2.connect(pg_conn_str)
    conn.autocommit = True

    try:
        with conn.cursor() as cur:
            # 1. Tenants
            tenants = list({(v.tenant_id, f"Tenant {v.tenant_id[:8]}") for v in vehicles})
            execute_values(
                cur,
                "INSERT INTO tenant (tenant_id, name) VALUES %s ON CONFLICT (tenant_id) DO NOTHING;",
                tenants,
            )
            logger.info("Seeded %d tenants.", len(tenants))

            # 2. Fleets
            fleets = list({(v.fleet_id, v.tenant_id, f"Fleet {v.fleet_id[:8]}") for v in vehicles})
            execute_values(
                cur,
                "INSERT INTO fleet (fleet_id, tenant_id, name) VALUES %s ON CONFLICT (fleet_id) DO UPDATE SET tenant_id = EXCLUDED.tenant_id;",
                fleets,
            )
            logger.info("Seeded %d fleets.", len(fleets))

            # 3. Drivers
            drivers = list({(v.driver_id, v.tenant_id, f"Driver {v.driver_id[:8]}", f"LIC-{v.vin[-6:]}") for v in vehicles if v.driver_id})
            execute_values(
                cur,
                "INSERT INTO driver (driver_id, tenant_id, name, license_number) VALUES %s ON CONFLICT (driver_id) DO NOTHING;",
                drivers,
            )
            logger.info("Seeded %d drivers.", len(drivers))

            # 4. Vehicles
            vehicle_rows = [
                (
                    v.vehicle_id,
                    v.fleet_id,
                    v.vin,
                    v.oem_id,
                    v.vehicle_type,
                    v.make,
                    v.model,
                    v.year,
                    # Registration data (observable): in service age_years before the simulation start.
                    (sim_start - timedelta(days=round(v.age_years * 365.25))).date(),
                    v.odometer_km,
                )
                for v in vehicles
            ]
            execute_values(
                cur,
                """
                INSERT INTO vehicle (
                    vehicle_id, fleet_id, vin, oem_id, vehicle_type, make, model, year, manufacture_date, current_odometer_km
                ) VALUES %s
                ON CONFLICT (vin) DO UPDATE SET
                    fleet_id = EXCLUDED.fleet_id,
                    vehicle_type = EXCLUDED.vehicle_type,
                    make = EXCLUDED.make,
                    model = EXCLUDED.model,
                    year = EXCLUDED.year,
                    manufacture_date = EXCLUDED.manufacture_date,
                    current_odometer_km = EXCLUDED.current_odometer_km;
                """,
                vehicle_rows,
            )
            logger.info("Seeded %d vehicles.", len(vehicle_rows))

            # 5. Vehicle Components
            component_rows = []
            for v in vehicles:
                for comp_type in ["BRAKE", "POWERTRAIN", "BATTERY"]:
                    comp_id = v.vehicle_component_ids.get(comp_type)
                    if comp_id:
                        is_active = comp_type in v.components
                        status = "ACTIVE" if is_active else "NOT_APPLICABLE"
                        component_rows.append((comp_id, v.vehicle_id, comp_type, status))

            execute_values(
                cur,
                """
                INSERT INTO vehicle_component (
                    vehicle_component_id, vehicle_id, component, status, installed_at
                ) VALUES %s
                ON CONFLICT (vehicle_id, component) DO UPDATE SET
                    status = EXCLUDED.status;
                """,
                component_rows,
                template="(%s, %s, %s, %s, now())",
            )
            logger.info("Seeded %d vehicle components.", len(component_rows))

    finally:
        conn.close()

    logger.info("Population seeding completed successfully.")


def main():
    parser = argparse.ArgumentParser(description="Seed FleetPulse fleet population into PostgreSQL")
    parser.add_argument("--config", type=str, default="configs/offline_train.yaml", help="Path to config YAML")
    parser.add_argument("--postgres-host", type=str, default=os.getenv("POSTGRES_HOST", "localhost"))
    parser.add_argument("--postgres-port", type=str, default=os.getenv("POSTGRES_PORT", "5434"))
    parser.add_argument("--postgres-db", type=str, default=os.getenv("POSTGRES_DB", "fleetpulse"))
    parser.add_argument("--postgres-user", type=str, default=os.getenv("POSTGRES_USER", "fleetpulse"))
    parser.add_argument("--postgres-password", type=str, default=os.getenv("POSTGRES_PASSWORD", "fleetpulse"))
    args = parser.parse_args()

    conn_str = f"host={args.postgres_host} port={args.postgres_port} dbname={args.postgres_db} user={args.postgres_user} password={args.postgres_password}"
    seed_database(args.config, conn_str)


if __name__ == "__main__":
    main()
