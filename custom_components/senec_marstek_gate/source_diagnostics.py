"""Inspect HA's in-memory states without claiming measurement freshness or control readiness.

These labels are inventory results only. No calibrated freshness limit, device
heartbeat, meter topology, sign or PV-surplus attestation is available here.
"""
from datetime import datetime
from math import isfinite

from .quality import REQUIRED


def inspect_sources(states) -> dict[str, str]:
    """Read only seven agreed entities; expose no live values in recorder state."""
    result = {}
    for entity, unit in REQUIRED.items():
        try:
            source = states.get(entity)
        except Exception:
            result[entity] = 'read_error'
            continue
        if source is None or getattr(source, 'entity_id', None) != entity:
            result[entity] = 'missing'
            continue
        attrs = getattr(source, 'attributes', {})
        if (not isinstance(attrs, dict)
                or attrs.get('unit_of_measurement') != unit
                or attrs.get('device_class') != ('battery' if unit == '%' else 'power')
                or attrs.get('state_class') != 'measurement'):
            result[entity] = 'metadata_mismatch'
            continue
        try:
            raw = source.state
            if isinstance(raw, bool):
                raise ValueError('boolean is not a measurement')
            value = float(raw)
            if not isfinite(value) or (unit == '%' and not 0 <= value <= 100):
                raise ValueError('out of range')
        except (ValueError, TypeError, OverflowError, AttributeError):
            result[entity] = 'invalid_value'
            continue
        reported = getattr(source, 'last_reported', None)
        if not isinstance(reported, datetime) or reported.tzinfo is None:
            result[entity] = 'missing_report_timestamp'
            continue
        # Presence of a timestamp is not a verified device heartbeat or freshness.
        result[entity] = 'observed_not_attested'
    return result
