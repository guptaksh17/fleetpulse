"""
Topology Unit Test (Requirement A1).
Asserts that no service in docker-compose.yml consumes and produces on the same topic.
"""

import sys
import os
import unittest
try:
    import yaml
except ImportError:
    yaml = None

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fallback_parse_compose_env(compose_path: str) -> dict:
    """Extract services and their environment variables without PyYAML."""
    services = {}
    current_service = None
    in_env = False
    with open(compose_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" "))
            if indent == 2 and line.strip().endswith(":") and not stripped.startswith("#"):
                current_service = stripped[:-1]
                services[current_service] = {"environment": {}}
                in_env = False
            elif indent == 4 and stripped == "environment:":
                in_env = True
            elif in_env:
                if indent == 6 and ":" in stripped and not stripped.startswith("#"):
                    k, v = stripped.split(":", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    services[current_service]["environment"][k] = v
                elif indent <= 4:
                    in_env = False
    return {"services": services}


class TestTopicTopology(unittest.TestCase):
    def test_no_service_consumes_and_produces_on_same_topic(self):
        compose_path = os.path.join(BASE_DIR, "docker-compose.yml")
        if yaml:
            with open(compose_path, "r", encoding="utf-8") as f:
                compose_cfg = yaml.safe_load(f)
        else:
            compose_cfg = _fallback_parse_compose_env(compose_path)

        services = compose_cfg.get("services", {})
        self.assertIn("identity-resolver", services)
        self.assertIn("normalization", services)
        self.assertIn("stream-processor", services)
        self.assertIn("rule-engine", services)

        # Check identity-resolver
        ir_env = services["identity-resolver"]["environment"]
        ir_in = ir_env.get("KAFKA_INBOUND_TOPIC")
        ir_out = ir_env.get("KAFKA_OUTBOUND_TOPIC")
        self.assertNotEqual(ir_in, ir_out, "Identity resolver must not produce to its consumed topic!")
        self.assertEqual(ir_in, "oem.inbound")
        self.assertEqual(ir_out, "vehicle.raw")

        # Check normalization
        norm_env = services["normalization"]["environment"]
        norm_in = norm_env.get("KAFKA_RAW_TOPIC")
        norm_out = norm_env.get("KAFKA_NORMALIZED_TOPIC")
        self.assertNotEqual(norm_in, norm_out, "Normalizer must not produce to its consumed topic!")
        self.assertEqual(norm_in, "vehicle.raw")
        self.assertEqual(norm_out, "vehicle.normalized")

        # Check rule-engine
        re_env = services["rule-engine"]["environment"]
        re_in = re_env.get("KAFKA_NORMALIZED_TOPIC")
        re_out = re_env.get("KAFKA_ALERTS_TOPIC")
        self.assertNotEqual(re_in, re_out, "Rule engine must not produce to its consumed topic!")
        self.assertEqual(re_in, "vehicle.normalized")
        self.assertEqual(re_out, "vehicle.alerts")

        # Check simulator
        sim_env = services["simulator"]["environment"]
        sim_out = sim_env.get("KAFKA_TOPIC")
        self.assertEqual(sim_out, "oem.inbound")


if __name__ == "__main__":
    unittest.main()
