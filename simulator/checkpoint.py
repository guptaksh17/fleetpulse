"""
Checkpoint Management for FleetPulse Simulator.
Saves and restores complete simulation state (sim_time, step_index, config_hash,
RNG bit generator states, and vehicle/component states) to guarantee exact determinism.
"""

from datetime import datetime, timezone
import json
import logging
import os
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger("simulator.checkpoint")


class CheckpointManager:
    @staticmethod
    def save_checkpoint(
        filepath: str,
        sim_time: datetime,
        step_index: int,
        config_hash: str,
        rng_streams,  # SimulationRNG instance
        vehicles: List,
    ):
        """
        Serializes full simulator state to JSON file atomically.
        """
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        tmp_path = f"{filepath}.tmp"

        checkpoint_data = {
            "version": "1.0",
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "sim_time": sim_time.isoformat(),
            "step_index": step_index,
            "config_hash": config_hash,
            "rng_states": {
                name: rng.bit_generator.state
                for name, rng in rng_streams.get_all().items()
            },
            "vehicles": [v.get_state() for v in vehicles],
        }

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(checkpoint_data, f, indent=2)

        os.replace(tmp_path, filepath)
        logger.info("Saved simulator checkpoint to %s (step: %d, sim_time: %s)", filepath, step_index, sim_time)

    @staticmethod
    def load_checkpoint(filepath: str) -> Optional[Dict[str, Any]]:
        """
        Loads simulator state from JSON file if it exists.
        """
        if not os.path.exists(filepath):
            logger.info("No checkpoint found at %s. Starting fresh.", filepath)
            return None

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        logger.info(
            "Loaded checkpoint from %s (step: %d, sim_time: %s)",
            filepath,
            data.get("step_index", 0),
            data.get("sim_time"),
        )
        return data

    @staticmethod
    def restore_simulator(
        checkpoint_data: Dict[str, Any],
        expected_config_hash: str,
        rng_streams,
        vehicles: List,
    ) -> Tuple[datetime, int]:
        """
        Restores RNG states and vehicle states from checkpoint data.
        Returns (sim_time, step_index).
        """
        ch_hash = checkpoint_data.get("config_hash")
        if ch_hash and ch_hash != expected_config_hash:
            logger.warning(
                "Config hash mismatch in checkpoint! (expected: %s, found: %s). Simulation parameters may have changed.",
                expected_config_hash[:8],
                ch_hash[:8],
            )

        # Restore RNG states
        rng_states = checkpoint_data.get("rng_states", {})
        all_rngs = rng_streams.get_all()
        for name, state in rng_states.items():
            if name in all_rngs:
                all_rngs[name].bit_generator.state = state

        # Restore vehicle states
        vehicle_map = {v.vehicle_id: v for v in vehicles}
        for v_state in checkpoint_data.get("vehicles", []):
            v_id = v_state.get("vehicle_id")
            if v_id in vehicle_map:
                vehicle_map[v_id].set_state(v_state)

        sim_time = datetime.fromisoformat(checkpoint_data["sim_time"])
        step_index = int(checkpoint_data["step_index"])
        return sim_time, step_index
