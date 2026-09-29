"""Loads configs/features.yaml into a small immutable structure."""

from dataclasses import dataclass, field
import os
from typing import Dict, Tuple

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.getenv("FEATURES_CONFIG", os.path.join(REPO_ROOT, "configs", "features.yaml"))


@dataclass(frozen=True)
class Window:
    name: str
    seconds: int
    bucket_seconds: int

    @property
    def n_buckets(self) -> int:
        return self.seconds // self.bucket_seconds


@dataclass(frozen=True)
class FeatureConfig:
    schema_version: str
    snapshot_interval_seconds: int
    windows: Tuple[Window, ...]
    retention_extra_seconds: int
    thresholds: Dict[str, float] = field(default_factory=dict)
    redis_key_prefix: str = "fs"
    state_ttl_seconds: int = 604800

    @property
    def bucket_widths(self) -> Tuple[int, ...]:
        return tuple(sorted({w.bucket_seconds for w in self.windows}))

    def retention_seconds(self, bucket_seconds: int) -> int:
        longest = max(w.seconds for w in self.windows if w.bucket_seconds == bucket_seconds)
        return longest + self.retention_extra_seconds

    @property
    def max_window_seconds(self) -> int:
        return max(w.seconds for w in self.windows)

    @property
    def late_event_horizon_seconds(self) -> int:
        return self.max_window_seconds + self.retention_extra_seconds


def load_feature_config(path: str = DEFAULT_PATH) -> FeatureConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    windows = tuple(
        Window(name, int(w["seconds"]), int(w["bucket_seconds"])) for name, w in raw["windows"].items()
    )
    snap = int(raw["snapshot_interval_seconds"])
    for w in windows:
        if w.seconds % w.bucket_seconds or snap % w.bucket_seconds:
            raise ValueError(f"window {w.name}: bucket width must divide the window and the snapshot interval")
    redis_cfg = raw.get("redis", {})
    return FeatureConfig(
        schema_version=str(raw["schema_version"]),
        snapshot_interval_seconds=snap,
        windows=windows,
        retention_extra_seconds=int(raw.get("retention_extra_seconds", 3600)),
        thresholds={k: float(v) for k, v in raw["thresholds"].items()},
        redis_key_prefix=redis_cfg.get("key_prefix", "fs"),
        state_ttl_seconds=int(redis_cfg.get("state_ttl_seconds", 604800)),
    )
