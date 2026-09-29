# ADR 0004: Exact fixed feature windows built from time buckets

**Status:** accepted (Phase 4)

## Context
The same features must be produced in streaming (live inference) and offline (training), or the
model silently degrades (training/serving skew).

## Options
1. EWMA or decayed aggregates. Cheap, but not reproducible exactly offline, and the effective
   window is fuzzy.
2. Store raw events per window and recompute. Exact, but memory grows with the event rate.
3. Mergeable statistics (count, sum, sumsq, min, max, first, last) in fixed buckets, merged at
   snapshot time.

## Decision
Option 3.
- Windows are 5m, 1h and 24h, half-open [t - W, t).
- Buckets are 60 s wide (900 s for the 24h window). Every bucket width divides the hourly
  snapshot interval, so bucket merges equal the exact windows.
- One shared statistic definition and one shared feature function serve both paths.
- Inputs are quantized to float32, so live events and database rows give bit-identical features.

## Consequences
- Parity is proven: 20 vehicles x 10 days, 803,279 values, 0 mismatches, max relative difference
  1.9e-10, including with duplicate replay and a crash and restart.
- Redis memory is 0.14 MB per vehicle with the packed-bucket layout, down from 0.36 MB. Further
  mitigations are in `docs/feature-spec.md`.
- Snapshots are only defined on the hour.
