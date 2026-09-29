"""DTC registry lookup (contracts/dtc/dtc_registry.yaml): code -> component."""

import os
from functools import lru_cache
from typing import Dict

import yaml

from .config import REPO_ROOT

DEFAULT_REGISTRY = os.getenv("DTC_REGISTRY_PATH", os.path.join(REPO_ROOT, "contracts", "dtc", "dtc_registry.yaml"))
COMPONENTS = ("BRAKE", "POWERTRAIN", "BATTERY")


@lru_cache(maxsize=4)
def dtc_component_map(path: str = DEFAULT_REGISTRY) -> Dict[str, str]:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return {code: spec["component"] for code, spec in raw["codes"].items()}
