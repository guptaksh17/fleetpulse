"""
Deduplication Stage for Stream Processor.
Combines in-batch deduplication, rotating Bloom filter fast-path, and Redis authoritative store.
Includes cold-start guard and strict error handling policies.
"""

import logging
import time
from typing import Dict, List, Optional, Set
from .bloom import RotatingBloomFilter

logger = logging.getLogger("stream-processor.dedup")


class DedupStage:
    def __init__(
        self,
        redis_client,
        window_seconds: int = 900,
        bloom_capacity: int = 1_000_000,
        bloom_fp_rate: float = 0.01,
        key_prefix: str = "dedup",
    ):
        self.redis = redis_client
        self.key_prefix = key_prefix
        self.window_seconds = window_seconds
        self.bloom = RotatingBloomFilter(
            capacity=bloom_capacity,
            fp_rate=bloom_fp_rate,
            window_seconds=float(window_seconds),
        )

        self._live_since = time.time()
        self.counters: Dict[str, int] = {
            "accepted": 0,
            "duplicates_dropped": 0,
            "bloom_fast_path": 0,
            "redis_reads": 0,
            "bloom_untrusted_checks": 0,
        }

    def reset_cold_start(self):
        """Called on startup and Kafka partition rebalances."""
        self._live_since = time.time()
        logger.info("Cold-start guard reset. Bloom filter untrusted for next %ds.", self.window_seconds)

    def bloom_trusted(self) -> bool:
        """Bloom is only trusted after running for at least one full window epoch."""
        return (time.time() - self._live_since) >= self.window_seconds

    @staticmethod
    def get_identity(event: dict) -> str:
        return f"{event['vehicle_id']}:{event['seq']}"

    def get_redis_key(self, identity: str) -> str:
        return f"{self.key_prefix}:{identity}"

    def is_duplicate(self, identity: str, in_batch_set: Set[str]) -> bool:
        """
        Deduplication decision tree:
        1. In-batch check
        2. Fast-path Bloom check (if trusted and absent)
        3. Authoritative Redis check
        """
        if identity in in_batch_set:
            self.counters["duplicates_dropped"] += 1
            return True

        if self.bloom_trusted() and (identity not in self.bloom):
            # Fast path: absent from Bloom and Bloom has seen a full epoch
            self.counters["bloom_fast_path"] += 1
            return False

        if not self.bloom_trusted():
            self.counters["bloom_untrusted_checks"] += 1

        self.counters["redis_reads"] += 1
        redis_key = self.get_redis_key(identity)

        # Redis failure policy: do not swallow connection errors.
        # Calling layer will catch, backoff, and avoid committing offsets.
        exists = bool(self.redis.exists(redis_key))
        if exists:
            self.counters["duplicates_dropped"] += 1
        return exists

    def mark_seen(self, identities: List[str]):
        """
        Record accepted identities as seen:
        1. Pipelined Redis SET key 1 EX window
        2. In-memory Bloom filter insertion
        """
        if not identities:
            return

        pipeline = self.redis.pipeline()
        for identity in identities:
            redis_key = self.get_redis_key(identity)
            pipeline.set(redis_key, "1", ex=self.window_seconds)

        # Execute pipelined write to Redis
        pipeline.execute()

        # Add to current Bloom generation
        for identity in identities:
            self.bloom.add(identity)

        self.counters["accepted"] += len(identities)

    def mark_seen_bloom_only(self, identities: List[str]):
        """
        Used when the feature engine's Lua script has already set the Redis dedup keys
        atomically with the feature update: only the in-memory Bloom filter and counters
        are updated here.
        """
        for identity in identities:
            self.bloom.add(identity)
        self.counters["accepted"] += len(identities)

    def get_and_reset_counters(self) -> Dict[str, int]:
        snapshot = dict(self.counters)
        return snapshot
