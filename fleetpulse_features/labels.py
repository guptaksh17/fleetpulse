"""
Label definition (label_definition_version l1).

For a snapshot of component c at prediction time t:
  label_7d = 1 if a MAINTENANCE_REQUIRED or FAILURE event of c has t < occurred_at <= t + horizon.
Exclusions:
  in_gap: the latest event of c with occurred_at <= t is MAINTENANCE_REQUIRED or FAILURE,
          i.e. the component is awaiting service at t.
  tail:   t + horizon > data_end, so the horizon is not fully observed.
"""

from datetime import timedelta
from typing import Optional

import numpy as np
import pandas as pd

LABEL_DEFINITION_VERSION = "l1"
TRIGGER_TYPES = ("MAINTENANCE_REQUIRED", "FAILURE")
DEFAULT_HORIZON = pd.Timedelta(days=7)


def label_snapshots(
    snapshots: pd.DataFrame,
    events: pd.DataFrame,
    data_end,
    horizon: pd.Timedelta = DEFAULT_HORIZON,
    key: str = "vehicle_component_id",
    time_col: str = "feature_ts",
) -> pd.DataFrame:
    """
    snapshots: columns [key, time_col] (tz-aware UTC timestamps).
    events:    columns [key, event_id, event_type, occurred_at].
    Returns a frame aligned to snapshots.index with columns
      label_7d (int8), label_next_event_id (str or None), label_hours_to_event (float, NaN if none),
      in_gap (bool), tail (bool).
    """
    n = len(snapshots)
    label = np.zeros(n, dtype=np.int8)
    next_id = np.full(n, None, dtype=object)
    hours_to = np.full(n, np.nan, dtype=np.float64)
    in_gap = np.zeros(n, dtype=bool)

    t_all = _to_ns(snapshots[time_col])
    horizon_ns = int(pd.Timedelta(horizon).value)
    data_end_ns = int(pd.Timestamp(data_end).value)
    tail = (t_all + horizon_ns) > data_end_ns

    if len(events):
        ev = events.copy()
        ev["_ts"] = _to_ns(ev["occurred_at"])
        ev["_trig"] = ev["event_type"].isin(TRIGGER_TYPES).to_numpy()
        ev = ev.sort_values([key, "_ts", "event_type"], kind="mergesort")
        groups = {k: g for k, g in ev.groupby(key, sort=False)}
    else:
        groups = {}

    positions = np.arange(n)
    for comp, idx in snapshots.groupby(key, sort=False).indices.items():
        g = groups.get(comp)
        if g is None:
            continue
        t = t_all[idx]
        ts_all = g["_ts"].to_numpy()
        trig_all = g["_trig"].to_numpy()

        # In-gap: latest event with occurred_at <= t is a trigger event.
        last_pos = np.searchsorted(ts_all, t, side="right") - 1
        has_prev = last_pos >= 0
        in_gap[positions[idx][has_prev]] = trig_all[last_pos[has_prev]]

        # Positive: first trigger event with occurred_at > t within the horizon.
        trig = g[g["_trig"]]
        if len(trig) == 0:
            continue
        ts_trig = trig["_ts"].to_numpy()
        ids_trig = trig["event_id"].astype(str).to_numpy()
        nxt = np.searchsorted(ts_trig, t, side="right")
        valid = nxt < len(ts_trig)
        nxt_ts = np.where(valid, ts_trig[np.minimum(nxt, len(ts_trig) - 1)], np.iinfo(np.int64).max)
        pos = valid & (nxt_ts <= t + horizon_ns)
        sel = positions[idx][pos]
        label[sel] = 1
        next_id[sel] = ids_trig[nxt[pos]]
        hours_to[sel] = (nxt_ts[pos] - t[pos]) / 3.6e12

    return pd.DataFrame(
        {
            "label_7d": label,
            "label_next_event_id": pd.Series(next_id, index=snapshots.index, dtype=object),
            "label_hours_to_event": hours_to,
            "in_gap": in_gap,
            "tail": tail,
        },
        index=snapshots.index,
    )


def _to_ns(series: pd.Series) -> np.ndarray:
    s = pd.to_datetime(series, utc=True)
    return s.to_numpy(dtype="datetime64[ns]").astype(np.int64)
