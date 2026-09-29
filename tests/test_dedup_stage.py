"""
Unit tests for DedupStage with fake Redis client.
Validates:
- New event accepted
- Duplicate rejected
- In-batch duplicate rejected
- Cold-start guard rejects event when key already exists in Redis
- Trusted fast path performs no Redis read
- Mark-seen writes to Redis and Bloom
- Redis outage policy: raises and accepts nothing
"""

import sys
import os
import time
import unittest
from unittest.mock import MagicMock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "services", "stream-processor"))

from stream_processor.dedup.stage import DedupStage


class FakeRedisPipeline:
    def __init__(self, fake_redis):
        self.fake_redis = fake_redis
        self.commands = []

    def set(self, key, val, ex=None):
        self.commands.append((key, val, ex))
        return self

    def execute(self):
        for key, val, ex in self.commands:
            self.fake_redis._store[key] = val
        self.commands.clear()
        return True


class FakeRedis:
    def __init__(self):
        self._store = {}
        self.read_count = 0
        self.should_fail = False

    def exists(self, key):
        if self.should_fail:
            raise ConnectionError("Simulated Redis connection failure")
        self.read_count += 1
        return 1 if key in self._store else 0

    def pipeline(self):
        if self.should_fail:
            raise ConnectionError("Simulated Redis connection failure")
        return FakeRedisPipeline(self)


class TestDedupStage(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.stage = DedupStage(
            redis_client=self.redis,
            window_seconds=10,
            bloom_capacity=10_000,
            bloom_fp_rate=0.01,
        )

    def test_new_event_accepted_and_duplicate_rejected(self):
        identity = "veh-1:100"
        in_batch = set()

        # 1. New event should not be duplicate
        is_dup = self.stage.is_duplicate(identity, in_batch)
        self.assertFalse(is_dup)

        # Mark seen after sink write
        self.stage.mark_seen([identity])

        # 2. Same event queried again should be flagged duplicate
        is_dup_second = self.stage.is_duplicate(identity, in_batch)
        self.assertTrue(is_dup_second)

    def test_in_batch_duplicate_rejected(self):
        identity = "veh-1:101"
        in_batch = {identity}  # Seen earlier in the same batch

        # Should be rejected immediately without querying Redis
        initial_reads = self.redis.read_count
        is_dup = self.stage.is_duplicate(identity, in_batch)
        self.assertTrue(is_dup)
        self.assertEqual(self.redis.read_count, initial_reads)

    def test_cold_start_guard(self):
        # Seed key in Redis (from previous run before restart)
        identity = "veh-1:102"
        self.redis._store[self.stage.get_redis_key(identity)] = "1"

        # Fresh instance: Bloom filter is empty and cold-start guard is active
        self.assertFalse(self.stage.bloom_trusted())
        self.assertNotIn(identity, self.stage.bloom)

        # Because Bloom is untrusted, it must fall back to Redis and reject the duplicate
        is_dup = self.stage.is_duplicate(identity, set())
        self.assertTrue(is_dup)
        self.assertGreater(self.redis.read_count, 0)

    def test_trusted_fast_path_skips_redis(self):
        # Simulate time advancing past the cold-start window
        self.stage._live_since = time.time() - (self.stage.window_seconds + 5)
        self.assertTrue(self.stage.bloom_trusted())

        identity = "veh-1:103"
        initial_reads = self.redis.read_count

        # Fast path: identity absent in Bloom and Bloom is trusted
        is_dup = self.stage.is_duplicate(identity, set())
        self.assertFalse(is_dup)
        # Verify NO Redis read was made!
        self.assertEqual(self.redis.read_count, initial_reads)
        self.assertEqual(self.stage.counters["bloom_fast_path"], 1)

    def test_redis_outage_policy_raises(self):
        identity = "veh-1:104"
        self.redis.should_fail = True

        # When untrusted and Redis is down, it must raise and NOT accept
        with self.assertRaises(ConnectionError):
            self.stage.is_duplicate(identity, set())

        with self.assertRaises(ConnectionError):
            self.stage.mark_seen([identity])


if __name__ == "__main__":
    unittest.main()
