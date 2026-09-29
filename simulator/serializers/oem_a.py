"""
OEM-A v1 Telemetry Envelope Serializer.
Packages observable telemetry into the OEM-A envelope consumed by the identity resolver
and the normalizer (which dispatches on oem_id + schema_version).
"""

from datetime import datetime, timezone
from typing import Dict, Any


class OEMAEnvelopeSerializer:
    OEM_ID = "OEM_A"
    SCHEMA_VERSION = "1.0"

    @classmethod
    def serialize(cls, payload: Dict[str, Any], oem_id: str = OEM_ID) -> Dict[str, Any]:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"
        return {
            "received_at": now_iso,
            "oem_id": oem_id,
            "schema_version": cls.SCHEMA_VERSION,
            "payload": payload,
        }
