# Relational Model (PostgreSQL, 3NF)

```mermaid
erDiagram
  TENANT ||--o{ FLEET : owns
  TENANT ||--o{ APP_USER : "scopes (null = admin)"
  TENANT ||--o{ DRIVER : employs
  FLEET ||--o{ VEHICLE : contains
  OEM ||--o{ VEHICLE : manufactures
  OEM ||--o{ OEM_SCHEMA : publishes
  VEHICLE ||--o{ VEHICLE_COMPONENT : has
  VEHICLE ||--o{ TRIP : makes
  DRIVER ||--o{ TRIP : drives
  VEHICLE_COMPONENT ||--o{ MAINTENANCE_EVENT : "ground truth"
  VEHICLE_COMPONENT ||--o{ ALERT : raises
  VEHICLE_COMPONENT ||--o| COMPONENT_RISK : "latest P7d"
  COMPONENT_COST }o--o{ VEHICLE_COMPONENT : "prices (component, vehicle_type)"
  TENANT ||--o{ AUDIT_LOG : records

  TENANT { uuid tenant_id PK
           string name }
  FLEET { uuid fleet_id PK
          uuid tenant_id FK
          string name }
  VEHICLE { uuid vehicle_id PK
            uuid fleet_id FK
            string vin UK
            string oem_id FK
            string vehicle_type
            date manufacture_date }
  VEHICLE_COMPONENT { uuid vehicle_component_id PK
                      uuid vehicle_id FK
                      string component
                      string status }
  MAINTENANCE_EVENT { uuid event_id PK
                      uuid vehicle_component_id FK
                      string event_type
                      timestamptz occurred_at
                      float odometer_km }
  ALERT { uuid alert_id PK
          uuid vehicle_component_id FK
          string alert_type
          string source
          string status
          float risk_probability }
  COMPONENT_RISK { uuid vehicle_component_id PK
                   float p7d
                   float threshold
                   string model_version }
  COMPONENT_COST { string component PK
                   string vehicle_type PK
                   float direct_failure_cost
                   float expected_downtime_hours
                   float downtime_cost_per_hour }
  APP_USER { uuid user_id PK
             string email UK
             string role
             uuid tenant_id FK }
  AUDIT_LOG { uuid audit_id PK
              uuid tenant_id FK
              string actor_id
              string action
              timestamptz occurred_at }
  DRIVER { uuid driver_id PK
           uuid tenant_id FK
           string name
           string license_number }
  TRIP { uuid trip_id PK
         uuid vehicle_id FK
         uuid driver_id FK
         timestamptz started_at }
```

## Normal form

Every non-key attribute depends only on its table's key.
- **Tenant ownership** is never copied onto child rows. It is derived through
  vehicle -> fleet -> tenant, which avoids update anomalies when a fleet changes tenant.
- **Cost profiles** are keyed by (component, vehicle_type) rather than stored per component row.
  That removes about 300K redundant copies at the 100K-vehicle scale.
- **Alert deduplication** is a partial unique index on (vehicle_component_id, alert_type) where
  status = 'ACTIVE'. It gives one active alert per component and type without a separate lock table.

## Deliberate denormalisation

`maintenance_priority_mv` is a materialized read model (CQRS). It joins component_risk,
vehicle_component, vehicle, fleet and component_cost, precomputes expected_loss, and carries sorted
indexes for keyset pagination.

The scorer refreshes it with `REFRESH MATERIALIZED VIEW CONCURRENTLY` after each scoring pass. Reads
never block, and the queue is at most one scoring interval stale. The write model stays normalised;
only this read path is denormalised, and the before/after query plans in
`docs/algorithms-and-sql.md` justify it.

## Time-series stores (TimescaleDB)

- **`telemetry`:** a hypertable partitioned by `event_ts` (7-day chunks), with an index on
  (vehicle_id, event_ts DESC) and a unique index on (vehicle_id, seq, event_ts) for idempotent
  writes. In a multi-node deployment, space partitioning on vehicle_id spreads writes and avoids
  hot spots. Each vehicle emits at most 1 event per second, so no single key dominates.
- **`component_features`:** a hypertable on `feature_ts`, with primary key
  (vehicle_id, component, feature_ts) for idempotent snapshot writes.
