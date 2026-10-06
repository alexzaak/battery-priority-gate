"""Offline-only classification of already checked sensor values; no HA I/O."""
from dataclasses import dataclass
from math import isfinite
from typing import Mapping

from .quality import QualityReport, REQUIRED


@dataclass(frozen=True)
class Thresholds:
    senec_w: float
    venus_w: float
    grid_w: float


@dataclass(frozen=True)
class Directions:
    """Numerical directions only; not a production/control attestation."""
    senec: str
    venus: Mapping[int, str]
    grid: str
    ready: bool

    @property
    def pv_surplus_proven(self) -> bool:
        """No validated surplus/topology proof is available in this offline slice."""
        return False


def counterflow(directions: Directions) -> frozenset[int] | None:
    """Offline contradiction pattern; None means unknown, never a device command."""
    if (not isinstance(directions, Directions) or directions.ready is not True
            or directions.senec not in {'idle', 'charge', 'discharge'}
            or not isinstance(directions.venus, Mapping)
            or any(directions.venus.get(i) not in {'idle', 'charge', 'discharge'} for i in (1, 2))):
        return None
    return frozenset(i for i in (1, 2)
                     if (directions.senec == 'charge' and directions.venus[i] == 'discharge')
                     or (directions.senec == 'discharge' and directions.venus[i] == 'charge'))


def classify(quality: QualityReport, limits: Thresholds) -> Directions:
    if (not isinstance(quality, QualityReport) or not quality.ready
            or not isinstance(limits, Thresholds)
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not isfinite(v) or v <= 0
                   for v in (limits.senec_w, limits.venus_w, limits.grid_w))
            or any((s := quality.sources.get(eid)) is None or s.ok is not True
                   or isinstance(s.value, bool) or not isinstance(s.value, (int, float))
                   or not isfinite(s.value) for eid in REQUIRED)):
        return Directions('unknown', {1: 'unknown', 2: 'unknown'}, 'unknown', False)
    def direction(value, deadband, positive):
        if value > deadband:
            return positive
        if value < -deadband:
            return 'discharge'
        return 'idle'
    sources = quality.sources
    senec = direction(sources['sensor.senec_battery_state_power'].value, limits.senec_w, 'charge')
    venus = {i: direction(sources[f'sensor.marstek_venus_{i}_battery_power'].value, limits.venus_w, 'charge')
             for i in (1, 2)}
    grid_value = sources['sensor.senec_enfluri_net_power_total'].value
    grid = 'import' if grid_value > limits.grid_w else 'export' if grid_value < -limits.grid_w else 'neutral'
    return Directions(senec, venus, grid, True)
