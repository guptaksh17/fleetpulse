"""
High-performance Rotating Bloom Filter for Stream Processor Deduplication.
Two filters (current and previous) rotated on window_seconds epochs.
Uses Kirsch-Mitzenmacher double-hashing optimization over SHA-256.
"""

import hashlib
import math
import time
from typing import Optional


class SingleBloomFilter:
    """Fixed-capacity Bloom filter backed by a bytearray."""

    def __init__(self, capacity: int, fp_rate: float):
        if capacity <= 0 or fp_rate <= 0 or fp_rate >= 1:
            raise ValueError("capacity must be > 0 and 0 < fp_rate < 1")

        self.capacity = capacity
        self.fp_rate = fp_rate

        # Optimal bit count m and hash count k
        # m = - (n * ln(p)) / (ln(2)^2)
        # k = (m / n) * ln(2)
        self.num_bits = int(math.ceil(-(capacity * math.log(fp_rate)) / (math.log(2) ** 2)))
        self.num_hashes = int(math.ceil((self.num_bits / capacity) * math.log(2)))

        self.byte_count = (self.num_bits + 7) // 8
        self.bit_array = bytearray(self.byte_count)
        self.count = 0

    def _get_hashes(self, item: str):
        # Generate two 64-bit seed hashes from SHA-256
        h = hashlib.sha256(item.encode("utf-8")).digest()
        h1 = int.from_bytes(h[:8], byteorder="big", signed=False)
        h2 = int.from_bytes(h[8:16], byteorder="big", signed=False) | 1

        for i in range(self.num_hashes):
            yield (h1 + i * h2) % self.num_bits

    def add(self, item: str):
        for bit_index in self._get_hashes(item):
            self.bit_array[bit_index >> 3] |= 1 << (bit_index & 7)
        self.count += 1

    def __contains__(self, item: str) -> bool:
        for bit_index in self._get_hashes(item):
            if not (self.bit_array[bit_index >> 3] & (1 << (bit_index & 7))):
                return False
        return True


class RotatingBloomFilter:
    """
    Rotating Bloom Filter maintaining two generations: current and previous.
    Epoch = window_seconds.
    Reads query both generations. Writes go to current.
    On rotation, previous is discarded and current becomes previous.
    """

    def __init__(self, capacity: int = 1_000_000, fp_rate: float = 0.01, window_seconds: float = 900.0):
        self.capacity = capacity
        self.fp_rate = fp_rate
        self.window_seconds = window_seconds

        self.current = SingleBloomFilter(capacity, fp_rate)
        self.previous: Optional[SingleBloomFilter] = None
        self.last_rotation = time.time()

    def check_rotation(self, now: Optional[float] = None):
        current_time = now if now is not None else time.time()
        if current_time - self.last_rotation >= self.window_seconds:
            self.rotate(current_time)

    def rotate(self, now: Optional[float] = None):
        self.previous = self.current
        self.current = SingleBloomFilter(self.capacity, self.fp_rate)
        self.last_rotation = now if now is not None else time.time()

    def add(self, item: str):
        self.check_rotation()
        self.current.add(item)

    def __contains__(self, item: str) -> bool:
        self.check_rotation()
        if item in self.current:
            return True
        if self.previous is not None and item in self.previous:
            return True
        return False
