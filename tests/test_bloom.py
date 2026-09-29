"""
Unit tests for Rotating Bloom Filter.
Validates:
- Zero false negatives over 100K inserts.
- Measured false-positive rate within 2x of configured.
- Rotation lifecycle: item found after 1 rotation, absent after 2 rotations.
"""

import sys
import os
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "services", "stream-processor"))

from stream_processor.dedup.bloom import SingleBloomFilter, RotatingBloomFilter


class TestBloomFilter(unittest.TestCase):
    def test_single_bloom_no_false_negatives_and_fp_rate(self):
        capacity = 100_000
        fp_rate = 0.01
        bloom = SingleBloomFilter(capacity=capacity, fp_rate=fp_rate)

        # 1. Insert 100K items and verify NO false negatives
        inserted = [f"item_{i}" for i in range(capacity)]
        for item in inserted:
            bloom.add(item)

        for item in inserted:
            self.assertIn(item, bloom, f"False negative detected for {item}!")

        # 2. Query 50K non-inserted items to measure empirical false-positive rate
        fp_count = 0
        test_uninserted_count = 50_000
        for i in range(capacity, capacity + test_uninserted_count):
            if f"item_{i}" in bloom:
                fp_count += 1

        empirical_fp_rate = fp_count / test_uninserted_count
        # Empirical rate should be reasonably within 2x of configured fp_rate (i.e. <= 0.02)
        self.assertLessEqual(
            empirical_fp_rate,
            fp_rate * 2.0,
            f"Empirical FP rate {empirical_fp_rate} exceeded 2x target {fp_rate}",
        )

    def test_rotating_bloom_lifecycle(self):
        capacity = 1_000
        fp_rate = 0.01
        window_seconds = 100.0  # mock epoch
        rot_bloom = RotatingBloomFilter(capacity=capacity, fp_rate=fp_rate, window_seconds=window_seconds)

        test_item = "vehicle_1:seq_42"
        rot_bloom.add(test_item)

        # Present immediately in current generation
        self.assertIn(test_item, rot_bloom)
        self.assertIn(test_item, rot_bloom.current)
        self.assertIsNone(rot_bloom.previous)

        # 1st rotation: current becomes previous, new current created
        rot_bloom.rotate()
        self.assertIn(test_item, rot_bloom)  # Still found via previous generation
        self.assertNotIn(test_item, rot_bloom.current)
        self.assertIn(test_item, rot_bloom.previous)

        # 2nd rotation: previous is discarded, new current created
        rot_bloom.rotate()
        self.assertNotIn(test_item, rot_bloom)  # Expired and absent from both filters


if __name__ == "__main__":
    unittest.main()
