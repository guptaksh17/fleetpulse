"""
DTC Rule Evaluator.
Evaluates canonical vehicle events against dtc_registry.yaml.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None

logger = logging.getLogger("rule-engine.rules")


def _fallback_yaml_load(content: str) -> dict:
    """Pure Python fallback parser for simple DTC registry YAML format."""
    codes = {}
    current_code = None
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line == "codes:":
            continue
        if line.endswith(":") and not any(k in line for k in ["component:", "severity:", "description:"]):
            current_code = line[:-1].strip()
            codes[current_code] = {}
        elif current_code and ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            codes[current_code][k] = v
    return {"codes": codes}


@dataclass
class DtcHit:
    dtc_code: str
    component: str
    severity: str
    description: str


class DtcRule:
    def __init__(self, registry_path: str):
        self.registry_path = registry_path
        self.registry: Dict[str, dict] = {}
        self.unknown_codes_count = 0
        self.load_registry()

    def load_registry(self):
        try:
            with open(self.registry_path, "r", encoding="utf-8") as f:
                content = f.read()
                if yaml:
                    data = yaml.safe_load(content)
                else:
                    data = _fallback_yaml_load(content)
                self.registry = data.get("codes", {})
            logger.info("Loaded %d DTC definitions from %s", len(self.registry), self.registry_path)
        except Exception as e:
            logger.error("Failed to load DTC registry from %s: %s", self.registry_path, e)
            raise

    def evaluate(self, event: dict) -> List[DtcHit]:
        hits: List[DtcHit] = []
        dtc_codes = event.get("dtc_codes", [])
        if not dtc_codes:
            return hits

        for code in dtc_codes:
            entry = self.registry.get(code)
            if not entry:
                self.unknown_codes_count += 1
                logger.warning("Unknown DTC code encountered: '%s'. Skipping.", code)
                continue

            hits.append(
                DtcHit(
                    dtc_code=code,
                    component=entry["component"],
                    severity=entry["severity"],
                    description=entry["description"],
                )
            )

        return hits
