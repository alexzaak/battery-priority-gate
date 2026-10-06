"""Pure offline control planner. Requests are *not* HA/device commands.

All attestation booleans and limits must be externally validated; this module
cannot prove physical topology, hardware heartbeat, safe 0 W, or writer origin.
"""
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Mapping

from .classify import Thresholds, classify, counterflow
from .gate import OWNERSHIP
from .quality import QualityReport, REQUIRED


@dataclass(frozen=True)
class CycleState:
    ownership: str = 'manual'
    stable_since: datetime | None = None
    stable_signature: tuple | None = None
    last_checked: datetime | None = None


@dataclass(frozen=True)
class Evidence:
    source_attested: bool
    pv_surplus_attested: bool
    exclusive: Mapping[int, bool]
    maintenance: bool
    handover_confirmed: Mapping[int, bool]


@dataclass(frozen=True)
class Limits:
    grid_deadband_w: float
    senec_deadband_w: float
    venus_deadband_w: float
    max_charge_w: Mapping[int, float]
    max_discharge_w: Mapping[int, float]
    stable_s: float
    soc_resume_pct: float


@dataclass(frozen=True)
class CycleResult:
    state: CycleState
    requests: Mapping[int, tuple[str, float | None]]
    reason: str


def _valid_limits(p: Limits) -> bool:
    if not isinstance(p, Limits):
        return False
    nums = (p.grid_deadband_w, p.senec_deadband_w, p.venus_deadband_w,
            p.stable_s, p.soc_resume_pct)
    if any(type(v) not in (int, float) or not isfinite(v) or v <= 0 for v in nums):
        return False
    if p.soc_resume_pct <= 12 or p.soc_resume_pct > 100:
        return False
    for limits in (p.max_charge_w, p.max_discharge_w):
        if not isinstance(limits, Mapping) or set(limits) != {1, 2}:
            return False
        if any(type(v) not in (int, float) or not isfinite(v) or v <= 0
               for v in limits.values()):
            return False
    return True


def _safe_request(device: int, evidence: Evidence) -> tuple[str, float | None]:
    """A stop *request* is meaningful only after exclusive handover."""
    if (evidence.exclusive.get(device) is True
            and evidence.handover_confirmed.get(device) is True
            and evidence.maintenance is False):
        return 'zero_intent', 0
    return 'warn', None


def evaluate(state: CycleState, quality: QualityReport, evidence: Evidence,
             limits: Limits, now: datetime) -> CycleResult:
    """Allocate a bounded, non-duplicated grid budget after continuous stability.

    No HA I/O, service calls, source auto-switch, physical attestation, or
    device writes. Caller must retain the returned state; restart resets it.
    """
    if not isinstance(state, CycleState) or state.ownership not in OWNERSHIP:
        return CycleResult(CycleState(), {i: ('warn', None) for i in (1, 2)}, 'unknown_owner')
    owned = OWNERSHIP[state.ownership]
    requests: dict[int, tuple[str, float | None]] = {
        i: ('hold', None) if i in owned else ('unmanaged', None) for i in (1, 2)
    }
    reset = CycleState(state.ownership)
    if not isinstance(evidence, Evidence) or type(evidence.maintenance) is not bool:
        return CycleResult(reset, {i: ('warn', None) for i in (1, 2)}, 'invalid_evidence')
    if evidence.maintenance:
        return CycleResult(CycleState(), {i: ('warn', None) for i in (1, 2)}, 'maintenance')
    if not isinstance(evidence.exclusive, Mapping) or not isinstance(evidence.handover_confirmed, Mapping):
        return CycleResult(reset, {i: ('warn', None) for i in (1, 2)}, 'unknown_writer')
    for i in owned:
        if evidence.exclusive.get(i) is not True or evidence.handover_confirmed.get(i) is not True:
            requests[i] = ('warn', None)
    if (type(evidence.source_attested) is not bool or not evidence.source_attested
            or not _valid_limits(limits) or not isinstance(quality, QualityReport)
            or set(quality.sources) != set(REQUIRED) or not quality.ready
            or not isinstance(now, datetime) or now.tzinfo is None):
        for i in owned:
            requests[i] = _safe_request(i, evidence)
        return CycleResult(reset, requests, 'invalid_quality_or_limits')

    directions = classify(quality, Thresholds(limits.senec_deadband_w,
                                              limits.venus_deadband_w,
                                              limits.grid_deadband_w))
    if not directions.ready:
        for i in owned:
            requests[i] = _safe_request(i, evidence)
        return CycleResult(reset, requests, 'unknown_direction')
    conflicts = counterflow(directions)
    if conflicts is None:
        for i in owned:
            requests[i] = _safe_request(i, evidence)
        return CycleResult(reset, requests, 'unknown_counterflow')
    for i in conflicts - owned:
        requests[i] = ('warn', None)
    if not owned:
        return CycleResult(reset, requests, 'manual')
    grid = quality.sources['sensor.senec_enfluri_net_power_total'].value
    pv = quality.sources['sensor.senec_solar_generated_power'].value
    if type(evidence.pv_surplus_attested) is not bool:
        for i in owned:
            requests[i] = _safe_request(i, evidence)
        return CycleResult(reset, requests, 'invalid_pv_evidence')
    charge = (directions.senec in ('idle', 'charge') and directions.grid == 'export'
              and pv > 0 and evidence.pv_surplus_attested)
    discharge = directions.senec == 'idle' and directions.grid == 'import'
    if not (charge or discharge) or conflicts & owned:
        for i in owned:
            requests[i] = _safe_request(i, evidence)
        return CycleResult(reset, requests, 'blocked_or_counterflow')
    mode = 'charge' if charge else 'discharge'
    signature = (state.ownership, mode)
    since = state.stable_since if state.stable_signature in (None, signature) else None
    last = state.last_checked
    if (state.stable_signature != signature or last is None or last.tzinfo is None
            or not 0 <= (now - last).total_seconds() <= 30
            or since is None or since.tzinfo is None or since > now):
        since = now
    next_state = CycleState(state.ownership, since, signature, now)
    if (now - since).total_seconds() < limits.stable_s:
        for i in owned:
            if directions.venus[i] != 'idle':
                requests[i] = _safe_request(i, evidence)
        return CycleResult(next_state, requests, 'settling')

    remaining = max(0.0, (-grid if charge else grid) - limits.grid_deadband_w)
    for i in (1, 2):
        if i not in owned or requests[i][0] == 'warn':
            continue
        soc = quality.sources[f'sensor.marstek_venus_{i}_battery_soc'].value
        if (not isinstance(soc, (int, float)) or not isfinite(soc)
                or (mode == 'discharge' and soc <= limits.soc_resume_pct)):
            if directions.venus[i] != 'idle':
                requests[i] = _safe_request(i, evidence)
            continue
        watts = min(remaining, (limits.max_charge_w if charge else limits.max_discharge_w)[i])
        if watts > 0:
            requests[i] = ('charge_intent' if charge else 'discharge_intent', watts)
            remaining -= watts
        elif directions.venus[i] != 'idle':
            requests[i] = _safe_request(i, evidence)
    return CycleResult(next_state, requests, 'candidate_not_authorization')
