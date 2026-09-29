# Algorithms, Data Structures and SQL Optimisation

## 1. Algorithms

| Problem | Algorithm and data structure | Time | Space | Code | Scale tested |
|---|---|---|---|---|---|
| Duplicate events at high rate | Rotating Bloom filter pair (current and previous 15-min epoch), double hashing g_i(x) = h1(x) + i*h2(x) mod m, backed by Redis `SET NX` as the authority | O(k) per event, k = 7 | m = -n ln p / (ln 2)^2: 103 MB per filter at n = 90M, p = 1 percent | `services/stream-processor/stream_processor/dedup/` | 56,481 deliveries with 7,463 duplicates, all dropped (parity run); 1,016 duplicates dropped live (smoke test) |
| Exact sliding windows (5m, 1h, 24h) in streaming | Fixed-width time buckets of mergeable statistics (count, sum, sumsq, min, max, first/last by (ts, seq)), one packed JSON field per bucket in a Redis hash. One atomic Lua script per event, doing one read and one write per bucket. Merge at snapshot time; prune with a sorted set of bucket indices | O(S) per event, S = 70 statistics; O(B x S) per snapshot, B <= 161 buckets | O(B x S) per vehicle: 139 KB measured (packed buckets; 358 KB before) | `fleetpulse_features/stats.py`, `.../features/engine.py`, `lua/apply_event.lua` | 20 vehicles x 10 days, 49,018 events, identical to offline |
| Same windows offline, vectorised | Group by (vehicle, hour block), then a sliding merge over 24 hourly blocks with `numpy.lib.stride_tricks.sliding_window_view`; forward/backward fill for first/last | O(N + H x S) per vehicle | O(H x S) | `scripts/build_features_offline.py` | 12.2M events -> 2.7M snapshots in 547 s |
| Leakage-safe labels | Per component, sorted event times; binary search (`numpy.searchsorted`) for the next trigger after t and the latest event at or before t | O((T + E) log E) | O(T + E) | `fleetpulse_features/labels.py` | 2.7M snapshots; exact agreement with a brute-force labeller on 3,000 random samples plus boundary cases |
| VIN validation | Regex `^[A-HJ-NPR-Z0-9]{17}$`, then the ISO 3779 check digit (weighted transliteration sum mod 11) | O(17) | O(1) | `services/identity-resolver/resolver.py` | 2,000 generated VINs pass; tampered VINs fail |
| DTC to component mapping | Hash map from the DTC registry | O(1) per code | O(codes) | `fleetpulse_features/dtc.py`, rule engine | all registry codes |
| Ranked maintenance queue | Expected-loss sort served by a B-tree index on the materialized read model, keyset pagination on (loss, id) | O(log n + k) per page | index O(n) | `db/postgres/migrations/006_priority_mv.sql`, `services/api/app/queries.py` | see section 2 |
| Calibrated risk | Gradient-boosted trees (histogram based) or logistic regression, with Platt scaling (1-D logistic regression on the logit of the raw score) | training O(N x F x iterations); inference O(trees x depth) | model size | `ml/train.py`, `ml/model.py` | 606K training rows per component |

### Pseudocode: per-event streaming update (Lua, atomic)

```
apply(event):
  if not SET dedup:{vid}:{seq} NX EX ttl: return 0          # already applied, change nothing
  for width in (60 s, 900 s):
    idx = ts // width
    for (op, stat, value) in contributions(event):          # zero and NaN contributions skipped
      field = idx:stat
      count -> HINCRBY; sum -> HINCRBYFLOAT
      min/max -> compare and set; first/last -> compare (ts, seq) and set
    ZADD live_indices idx idx
  meta.max_event_ts = max(...); meta.first_event_ts = min(...)
  meta.last_snapshot_ts = floor_hour(ts) if not set
  return 1
```

### Pseudocode: snapshot catch-up (per vehicle in the batch, duplicates included)

```
for t in hours(last_snapshot_ts + 1h .. max_event_ts):
  merged[w] = merge(buckets in [t - W, t)) for w in (5m, 1h, 24h)
  rows += features_from_window_stats(merged, static_context(t), component) for each ACTIVE component
write rows ON CONFLICT DO NOTHING; cache latest; then advance last_snapshot_ts; prune old buckets
```

## 2. SQL optimisation (EXPLAIN ANALYZE, before and after)

Measured on the local PostgreSQL 16 container with 100,708 vehicles and 302,124 components. The
plans and timings are pasted in section 3.

| Query | Before (ms) | After (ms) | Change made |
|---|---|---|---|
| Q1. Tenant priority queue, top 25 by expected loss (251,456 scored components; 151,047 in the tenant) | 881.5 | 0.45 | CQRS read model `maintenance_priority_mv` (precomputed expected loss) plus index (tenant_id, loss DESC, id DESC); the page becomes an index range scan |
| Q2. Vehicle list page 1,001 (100,708 vehicles) | 51.9 | 0.32 | Keyset pagination (`vehicle_id > last_seen`) instead of OFFSET 50000; no rows are read and discarded |
| Q3. Admin audit log page (ORDER BY occurred_at DESC, audit_id DESC) | 33.5 | 0.38 | Composite index `idx_audit_time_id` matching the sort and keyset predicate; replaces sequential scan + top-N sort (the audit table grows with every request) |

## 3. Query plans (EXPLAIN (ANALYZE, BUFFERS) output)

```
=== Q1 BEFORE: tenant priority page from the normalised view (join of 5 tables, sort by expected loss)
                                                                                                                                            QUERY PLAN                                                                                                                                             
---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
 Limit  (cost=3890.57..3890.64 rows=26 width=66) (actual time=881.343..881.347 rows=26 loops=1)
   Buffers: shared hit=779014 read=7996 written=4178
   ->  Sort  (cost=3890.57..3893.57 rows=1197 width=66) (actual time=881.342..881.344 rows=26 loops=1)
         Sort Key: ((round((COALESCE(CASE WHEN (c.component IS NULL) THEN NULL::double precision ELSE (r.p7d * (c.direct_failure_cost + (c.expected_downtime_hours * c.downtime_cost_per_hour))) END, '-1'::double precision))::numeric, 2))::double precision) DESC, vc.vehicle_component_id DESC
         Sort Method: top-N heapsort  Memory: 30kB
         Buffers: shared hit=779014 read=7996 written=4178
         ->  Nested Loop Left Join  (cost=675.50..3856.46 rows=1197 width=66) (actual time=27.402..865.127 rows=125774 loops=1)
               Buffers: shared hit=779008 read=7996 written=4178
               ->  Nested Loop  (cost=675.34..3797.93 rows=1197 width=54) (actual time=27.168..709.555 rows=125774 loops=1)
                     Buffers: shared hit=778994 read=7994 written=4178
                     ->  Nested Loop  (cost=674.92..3093.76 rows=1439 width=46) (actual time=26.767..438.531 rows=151047 loops=1)
                           Buffers: shared hit=200682 read=7391 written=4150
                           ->  Nested Loop  (cost=674.50..2770.79 rows=480 width=38) (actual time=26.744..146.400 rows=50349 loops=1)
                                 Buffers: shared hit=2040 read=3500 written=1849
                                 ->  Seq Scan on fleet f  (cost=0.00..12.62 rows=1 width=16) (actual time=0.017..0.022 rows=3 loops=1)
                                       Filter: (tenant_id = '00000000-0000-0000-0000-000000000001'::uuid)
                                       Rows Removed by Filter: 3
                                       Buffers: shared hit=1
                                 ->  Bitmap Heap Scan on vehicle v  (cost=674.50..2590.31 rows=16785 width=54) (actual time=23.377..47.084 rows=16783 loops=3)
                                       Recheck Cond: (fleet_id = f.fleet_id)
                                       Heap Blocks: exact=5118
                                       Buffers: shared hit=2039 read=3500 written=1849
                                       ->  Bitmap Index Scan on idx_vehicle_fleet  (cost=0.00..670.31 rows=16785 width=0) (actual time=23.098..23.098 rows=16783 loops=3)
                                             Index Cond: (fleet_id = f.fleet_id)
                                             Buffers: shared hit=1 read=420 written=31
                           ->  Index Scan using idx_vehicle_component_vehicle on vehicle_component vc  (cost=0.42..0.64 rows=3 width=40) (actual time=0.005..0.005 rows=3 loops=50349)
                                 Index Cond: (vehicle_id = v.vehicle_id)
                                 Buffers: shared hit=198642 read=3891 written=2301
                     ->  Index Scan using component_risk_pkey on component_risk r  (cost=0.42..0.49 rows=1 width=24) (actual time=0.002..0.002 rows=1 loops=151047)
                           Index Cond: (vehicle_component_id = vc.vehicle_component_id)
                           Buffers: shared hit=578312 read=603 written=28
               ->  Memoize  (cost=0.16..0.18 rows=1 width=160) (actual time=0.000..0.000 rows=1 loops=125774)
                     Cache Key: vc.component, v.vehicle_type
                     Cache Mode: logical
                     Hits: 125766  Misses: 8  Evictions: 0  Overflows: 0  Memory Usage: 2kB
                     Buffers: shared hit=14 read=2
                     ->  Index Scan using component_cost_pkey on component_cost c  (cost=0.15..0.17 rows=1 width=160) (actual time=0.024..0.025 rows=1 loops=8)
                           Index Cond: (((component)::text = (vc.component)::text) AND ((vehicle_type)::text = (v.vehicle_type)::text))
                           Buffers: shared hit=14 read=2
 Planning:
   Buffers: shared hit=429 read=22
 Planning Time: 8.999 ms
 Execution Time: 881.519 ms
(43 rows)
```

```
=== Q1 AFTER: same page from maintenance_priority_mv with index (tenant_id, loss DESC, id DESC)
                                                                              QUERY PLAN                                                                              
----------------------------------------------------------------------------------------------------------------------------------------------------------------------
 Limit  (cost=0.42..6.99 rows=26 width=66) (actual time=0.059..0.418 rows=26 loops=1)
   Buffers: shared hit=8 read=21
   ->  Index Scan using idx_priority_mv_tenant_loss on maintenance_priority_mv  (cost=0.42..26124.39 rows=103433 width=66) (actual time=0.058..0.414 rows=26 loops=1)
         Index Cond: (tenant_id = '00000000-0000-0000-0000-000000000001'::uuid)
         Buffers: shared hit=8 read=21
 Planning:
   Buffers: shared hit=113 read=18
 Planning Time: 1.919 ms
 Execution Time: 0.451 ms
(9 rows)
```

```
=== Q2 BEFORE: vehicle list, page 1,001 of 50 rows (OFFSET 50000)
                                                                    QUERY PLAN                                                                     
---------------------------------------------------------------------------------------------------------------------------------------------------
 Limit  (cost=6434.07..6440.50 rows=50 width=34) (actual time=51.659..51.707 rows=50 loops=1)
   Buffers: shared hit=50291
   ->  Nested Loop  (cost=0.57..12958.66 rows=100708 width=34) (actual time=0.093..49.995 rows=50050 loops=1)
         Buffers: shared hit=50291
         ->  Index Scan using vehicle_pkey on vehicle v  (cost=0.42..10446.95 rows=100708 width=50) (actual time=0.047..35.512 rows=50050 loops=1)
               Buffers: shared hit=50279
         ->  Memoize  (cost=0.15..0.17 rows=1 width=16) (actual time=0.000..0.000 rows=1 loops=50050)
               Cache Key: v.fleet_id
               Cache Mode: logical
               Hits: 50044  Misses: 6  Evictions: 0  Overflows: 0  Memory Usage: 1kB
               Buffers: shared hit=12
               ->  Index Only Scan using fleet_pkey on fleet f  (cost=0.14..0.16 rows=1 width=16) (actual time=0.008..0.008 rows=1 loops=6)
                     Index Cond: (fleet_id = v.fleet_id)
                     Heap Fetches: 6
                     Buffers: shared hit=12
 Planning:
   Buffers: shared hit=152
 Planning Time: 1.869 ms
 Execution Time: 51.855 ms
(19 rows)
```

```
=== Q2 AFTER: same page via keyset (vehicle_id > last seen)
                                                                 QUERY PLAN                                                                  
---------------------------------------------------------------------------------------------------------------------------------------------
 Limit  (cost=0.57..10.45 rows=50 width=34) (actual time=0.080..0.271 rows=50 loops=1)
   Buffers: shared hit=66
   ->  Nested Loop  (cost=0.57..10051.33 rows=50858 width=34) (actual time=0.079..0.266 rows=50 loops=1)
         Buffers: shared hit=66
         ->  Index Scan using vehicle_pkey on vehicle v  (cost=0.42..8782.33 rows=50858 width=50) (actual time=0.062..0.229 rows=50 loops=1)
               Index Cond: (vehicle_id > '7ed107ea-d1f3-553a-a017-19467ce36e25'::uuid)
               Buffers: shared hit=54
         ->  Memoize  (cost=0.15..0.17 rows=1 width=16) (actual time=0.001..0.001 rows=1 loops=50)
               Cache Key: v.fleet_id
               Cache Mode: logical
               Hits: 44  Misses: 6  Evictions: 0  Overflows: 0  Memory Usage: 1kB
               Buffers: shared hit=12
               ->  Index Only Scan using fleet_pkey on fleet f  (cost=0.14..0.16 rows=1 width=16) (actual time=0.003..0.003 rows=1 loops=6)
                     Index Cond: (fleet_id = v.fleet_id)
                     Heap Fetches: 6
                     Buffers: shared hit=12
 Planning:
   Buffers: shared hit=155
 Planning Time: 0.531 ms
 Execution Time: 0.322 ms
(20 rows)
```

```
=== Q3 BEFORE: admin audit page (ORDER BY occurred_at DESC, audit_id DESC), no matching index
                                                       QUERY PLAN                                                        
-------------------------------------------------------------------------------------------------------------------------
 Limit  (cost=453.26..453.39 rows=51 width=32) (actual time=33.469..33.475 rows=51 loops=1)
   Buffers: shared hit=264
   ->  Sort  (cost=453.26..464.52 rows=4503 width=32) (actual time=33.468..33.470 rows=51 loops=1)
         Sort Key: occurred_at DESC, audit_id DESC
         Sort Method: top-N heapsort  Memory: 32kB
         Buffers: shared hit=264
         ->  Seq Scan on audit_log  (cost=0.00..303.03 rows=4503 width=32) (actual time=0.018..32.963 rows=4759 loops=1)
               Buffers: shared hit=258
 Planning:
   Buffers: shared hit=99
 Planning Time: 0.346 ms
 Execution Time: 33.518 ms
(12 rows)

CREATE INDEX
```

```
=== Q3 AFTER: with idx_audit_time_id
                                                                QUERY PLAN                                                                
------------------------------------------------------------------------------------------------------------------------------------------
 Limit  (cost=0.28..8.63 rows=51 width=32) (actual time=0.330..0.358 rows=51 loops=1)
   Buffers: shared hit=27 read=2
   ->  Index Scan using idx_audit_time_id on audit_log  (cost=0.28..779.50 rows=4759 width=32) (actual time=0.329..0.353 rows=51 loops=1)
         Buffers: shared hit=27 read=2
 Planning:
   Buffers: shared hit=113 read=1
 Planning Time: 0.760 ms
 Execution Time: 0.380 ms
(8 rows)
```

