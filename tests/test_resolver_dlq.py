"""Identity resolver VIN validation (regex + ISO 3779 check digit) and dead-letter routing."""

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "services", "identity-resolver"))
import resolver  # noqa: E402
from simulator.vin import generate_unique_vins  # noqa: E402


class FakeProducer:
    def __init__(self):
        self.sent = []

    def send(self, topic, key=None, value=None):
        self.sent.append((topic, value))

    def flush(self):
        pass


class TestResolverValidation(unittest.TestCase):
    def test_valid_vins(self):
        vins = generate_unique_vins(np.random.default_rng(7), 2000)
        self.assertTrue(all(resolver.is_valid_vin(v) for v in vins))
        self.assertTrue(resolver.is_valid_vin("1HGCM82633A004352"))

    def test_invalid_vins(self):
        for bad in ["1HGCM82633A004353",  # wrong check digit
                    "1HGCM82633A00435",   # 16 characters
                    "1HGCM82633A0043521",  # 18 characters
                    "IHGCM82633A004352",  # letter I not allowed
                    "1hgcm82633a004352",  # lower case
                    "", None, 12345]:
            self.assertFalse(resolver.is_valid_vin(bad), bad)

    def test_dead_letter_carries_reason_and_envelope(self):
        p = FakeProducer()
        env = {"payload": {"vin": "BAD"}}
        resolver.send_to_dlq(p, "vehicle.dlq", env, "INVALID_VIN", "oem.inbound")
        topic, value = p.sent[0]
        self.assertEqual(topic, "vehicle.dlq")
        self.assertEqual((value["reason"], value["source_topic"], value["envelope"]), ("INVALID_VIN", "oem.inbound", env))


if __name__ == "__main__":
    unittest.main()
