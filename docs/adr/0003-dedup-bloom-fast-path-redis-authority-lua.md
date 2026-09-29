# ADR 0003: Deduplication, with a Bloom fast path, Redis authority and an atomic Lua update

**Status:** accepted (Phase 2, extended in Phase 4)

## Context
OEM clouds and at-least-once delivery produce duplicates. The identity of an event is
(vehicle_id, seq). Checking Redis for every event costs a round trip, and the Phase 4 feature
engine must not double count a replayed event.

## Decision
1. A rotating Bloom filter (current and previous 15-minute epoch) skips the Redis read for keys
   never seen.
   - At 100K events/s over 15 minutes (n = 90M) and p = 1 percent, each filter needs about 103 MB
     with k = 7 hash functions.
   - The Bloom filter is untrusted for one full window after startup or a rebalance, because it
     starts empty.
2. Redis `dedup:{vehicle_id}:{seq}` is the authority.
3. With features enabled, a single Lua script per event does `SET NX EX`. If the key already
   exists, the script changes nothing; otherwise it applies all bucket updates. Marking the event
   seen and counting it therefore happen in one atomic step.
4. The mandatory batch order is:
   1. dedup read
   2. sink write
   3. Lua per event
   4. Bloom add
   5. snapshot step
   6. offset commit

## Consequences
- A crash at any point replays safely. `tests/test_feature_lua.py` and
  `scripts/verify_feature_parity.py` prove it with a crash between the Lua step and the snapshot
  write.
- A replay older than the dedup TTL (15 minutes wall clock) would double count features. The
  recovery is an offline rebuild followed by warm-up. This is documented in `docs/feature-spec.md`.
