#!/usr/bin/env python3
"""
Seed API users. Passwords come from the environment (see .env.example), never from the repo:
  FP_ADMIN_PASSWORD      admin@fleetpulse.local (ADMIN, all tenants)
  FP_MANAGER_PASSWORD    manager@fleetpulse.local (FLEET_MANAGER, tenant 00000000-...-0001)
  FP_VIEWER_PASSWORD     viewer@fleetpulse.local (VIEWER, second tenant)
"""
import os
import sys
import uuid

import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "services", "api"))
from app.security import hash_password  # noqa: E402

DSN = os.getenv("POSTGRES_DSN", "host=localhost port=5434 dbname=fleetpulse user=fleetpulse password=fleetpulse")
T1 = "00000000-0000-0000-0000-000000000001"
T2 = str(uuid.uuid5(uuid.NAMESPACE_DNS, "tenant-1"))
USERS = [("admin@fleetpulse.local", "ADMIN", None, "FP_ADMIN_PASSWORD"),
         ("manager@fleetpulse.local", "FLEET_MANAGER", T1, "FP_MANAGER_PASSWORD"),
         ("viewer@fleetpulse.local", "VIEWER", T2, "FP_VIEWER_PASSWORD")]

with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
    for email, role, tenant, env in USERS:
        pw = os.getenv(env)
        if not pw:
            print(f"skip {email}: {env} not set")
            continue
        cur.execute("""INSERT INTO app_user (email, password_hash, role, tenant_id) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role, tenant_id = EXCLUDED.tenant_id""",
                    (email, hash_password(pw), role, tenant))
        print(f"seeded {email} ({role})")
