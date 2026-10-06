"""Fail-closed quality assessment of offline Home Assistant state snapshots.

No network, HA imports, action services, or production calibration are included.
"""
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Mapping

REQUIRED = {
    'sensor.senec_enfluri_net_power_total': 'W',
    'sensor.senec_battery_state_power': 'W',
    'sensor.senec_solar_generated_power': 'W',
    'sensor.marstek_venus_1_battery_power': 'W',
    'sensor.marstek_venus_2_battery_power': 'W',
    'sensor.marstek_venus_1_battery_soc': '%',
    'sensor.marstek_venus_2_battery_soc': '%',
}


@dataclass(frozen=True)
class SourceQuality:
    ok: bool
    reason: str
    value: float | None = None


@dataclass(frozen=True)
class QualityReport:
    sources: Mapping[str, SourceQuality]

    @property
    def ready(self) -> bool:
        return all(item.ok for item in self.sources.values())


def _check(entity: str, unit: str, state: object, now: datetime,
           max_age_s: Mapping, attested: Mapping) -> SourceQuality:
    age_limit = max_age_s.get(entity)
    evidence = attested.get(entity)
    if (isinstance(age_limit, bool) or not isinstance(age_limit, (int, float))
            or not isfinite(age_limit) or age_limit <= 0
            or not isinstance(evidence, Mapping)
            or any(evidence.get(key) is not True for key in ('topology', 'sign', 'heartbeat'))):
        return SourceQuality(False, 'unvalidated_policy')
    if not isinstance(state, Mapping) or state.get('entity_id') != entity:
        return SourceQuality(False, 'missing_or_mismatched_entity')
    attrs = state.get('attributes')
    if not isinstance(attrs, Mapping) or (attrs.get('unit_of_measurement') != unit
            or attrs.get('device_class') != ('battery' if unit == '%' else 'power')
            or attrs.get('state_class') != 'measurement'):
        return SourceQuality(False, 'metadata_mismatch')
    try:
        raw = state.get('state')
        if isinstance(raw, bool) or raw is None or str(raw).strip() == '':
            raise ValueError('nonnumeric')
        value = float(raw)
        if not isfinite(value) or (unit == '%' and not 0 <= value <= 100):
            raise ValueError('invalid_range')
    except (ValueError, TypeError, OverflowError):
        return SourceQuality(False, 'invalid_value')
    try:
        timestamp = datetime.fromisoformat(state['last_reported'])
        age = (now - timestamp).total_seconds()
        if now.tzinfo is None or timestamp.tzinfo is None or not isfinite(age) or not 0 <= age <= age_limit:
            raise ValueError('stale_or_future')
    except (ValueError, TypeError, KeyError, OverflowError):
        return SourceQuality(False, 'invalid_or_stale_last_reported')
    return SourceQuality(True, 'ok', value)


def assess(states: Mapping, *, now: datetime, max_age_s: Mapping, attested: Mapping) -> QualityReport:
    """Classify required sources; no fallback to last_updated or source auto-switch."""
    if not isinstance(states, Mapping):
        states = {}
    if not isinstance(max_age_s, Mapping):
        max_age_s = {}
    if not isinstance(attested, Mapping):
        attested = {}
    return QualityReport({entity: _check(entity, unit, states.get(entity), now, max_age_s, attested)
                          for entity, unit in REQUIRED.items()})
