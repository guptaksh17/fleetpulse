"""
One Kafka batch through the stream processor, in the mandatory order:
  1. dedup read check (in-batch set, rotating Bloom fast path, Redis authority)
  2. sink write of accepted events (idempotent)
  3. mark seen: with the feature engine, one Lua script per accepted event sets the dedup key
     (NX) and applies bucket statistics atomically; otherwise pipelined SET EX
  4. Bloom add (inside step 3)
  5. snapshot step for every vehicle in the batch, duplicates included
The caller commits offsets afterwards. Shared by main.py and scripts/verify_feature_parity.py.
"""

import logging
import time
from typing import Callable, Dict, Iterable, List, Optional

logger = logging.getLogger("stream-processor.pipeline")


def retry_forever(fn, what: str, sleep=time.sleep):
    """Redis / database outage policy: halt and retry with backoff, never drop or commit."""
    delay = 1.0
    while True:
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - any failure blocks the batch
            logger.error("%s failed (%s). Retrying in %.0fs...", what, e, delay)
            sleep(delay)
            delay = min(30.0, delay * 2)


def process_batch(
    events: List[dict],
    dedup_stage,
    sink_write: Callable[[List[dict]], None],
    feature_engine=None,
    to_record: Optional[Callable[[dict], dict]] = None,
) -> Dict[str, int]:
    """events: dicts with at least vehicle_id and seq. Returns per-batch counts."""
    batch_seen = set()
    accepted: List[dict] = []
    identities: List[str] = []

    def dedup_check():
        batch_seen.clear()
        accepted.clear()
        identities.clear()
        for event in events:
            identity = dedup_stage.get_identity(event)
            if not dedup_stage.is_duplicate(identity, batch_seen):
                batch_seen.add(identity)
                accepted.append(event)
                identities.append(identity)

    retry_forever(dedup_check, "Dedup check")
    if accepted:
        retry_forever(lambda: sink_write(accepted), "Sink write")

    result = {"received": len(events), "accepted": len(accepted), "snapshots": 0}
    if feature_engine is not None:
        if accepted:
            records = [to_record(e) for e in accepted] if to_record else accepted
            res = retry_forever(lambda: feature_engine.apply_records(records), "Feature apply")
            dedup_stage.mark_seen_bloom_only(identities)
            result.update(res)
        vids = {e["vehicle_id"] for e in events}
        result["snapshots"] = retry_forever(lambda: feature_engine.snapshot_step(vids), "Snapshot step")
    elif identities:
        retry_forever(lambda: dedup_stage.mark_seen(identities), "Mark seen")
    return result
