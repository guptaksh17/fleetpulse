# ADR 0002: Polyglot storage, with a CAP choice per data kind

**Status:** accepted (Phase 1, extended in Phase 6)

## Context
The data has different shapes:
- business state needs ACID and foreign keys;
- telemetry is append-heavy time series;
- hot per-vehicle state needs sub-millisecond atomic updates.

## Options
1. One PostgreSQL for everything. Write amplification and index bloat at billions of rows, and
   analytic scans compete with API queries.
2. Cassandra for telemetry. Horizontal writes, but no joins or window SQL for batch analytics.
3. PostgreSQL + TimescaleDB + Redis.

## Decision
Option 3.
- **PostgreSQL (CP):** tenants, fleets, vehicles, components, alerts, audit, ground truth, risk and
  users. These need strong consistency: an alert must not be created twice, and an audit row must
  commit with its action.
- **TimescaleDB:** telemetry and feature snapshots, in hypertables partitioned by time.
- **Redis (AP):** dedup keys, window buckets, rate-limit counters and caches. On partition, Redis
  may lose recent keys; the consequence is bounded, at most a duplicate sink write, which ON CONFLICT
  absorbs.

## PACELC
- **Else (latency over consistency):** read models favour latency. The priority materialized view
  and the 5-second summary cache are allowed to be stale.
- **Partition:** the write path favours consistency. Postgres transactions, and a batch is never
  committed while Redis is unreachable.

## Consequences
- Three stores to operate.
- Tenant ownership stays normalised in Postgres; see `docs/er-diagram.md`.
