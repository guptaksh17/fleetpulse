# ADR 0001: Kafka as the event backbone, at-least-once with idempotent consumers

**Status:** accepted (Phase 1)

## Context
100K vehicles at 1 event/s means about 100K events/s, with 3x bursts. Consumers must survive
restarts and replay history.

## Options
1. RabbitMQ. Good routing, but weak replay and per-key ordering at this volume.
2. Kafka with exactly-once transactions. Correct, but adds latency and every sink would need to take
   part in the transactions.
3. Kafka at-least-once, with idempotent consumers and manual offset commits.

## Decision
Option 3.
- Topics are partitioned by VIN or vehicle_id, which keeps each vehicle's events in order.
- No service consumes and produces on the same topic.
- Offsets are committed only after every downstream effect has succeeded.

## Consequences
- Duplicates are expected, and every sink is idempotent:
  - Timescale uses a unique index with ON CONFLICT DO NOTHING;
  - Redis uses SET NX;
  - alerts use a partial unique index.
- Replay is safe and cheap.
- Exactly-once semantics exist only as an end-to-end property of the sinks, not of the broker.
