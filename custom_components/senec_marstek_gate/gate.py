"""Pure offline SENEC/Marstek gate core. No Home Assistant imports or I/O."""
from dataclasses import dataclass
from math import isfinite
from typing import Mapping

OWNERSHIP = {
    'manual': frozenset(),
    'gate_venus_1': frozenset({1}),
    'gate_venus_2': frozenset({2}),
    'gate_beide': frozenset({1, 2}),
}
BY_MEMBERS = {members: state for state, members in OWNERSHIP.items()}


@dataclass(frozen=True)
class Inputs:
    """Already validated classifications, NOT raw sensor readings.

    `verified` must be false unless all required source units, topology, sign,
    direction, freshness, stability and controller ownership are established.
    No production adapter currently exists to assert it true.
    """
    senec: str
    grid: str
    pv_surplus: bool
    verified: bool
    soc: Mapping[int, float | None]


def decide(ownership: str, x: Inputs) -> dict[int, str]:
    """Return intent candidates; never commands, watts or permission to actuate."""
    owned = OWNERSHIP.get(ownership)
    if owned is None:
        return {1: 'idle_alarm', 2: 'idle_alarm'}
    result = {device: 'unmanaged' if device not in owned else 'idle' for device in (1, 2)}
    valid = (x.verified is True and type(x.pv_surplus) is bool
             and x.senec in {'charge', 'idle', 'discharge'}
             and x.grid in {'import', 'neutral', 'export'}
             and not (x.grid == 'import' and x.pv_surplus))
    for device in owned:
        soc = x.soc.get(device)
        if not valid or isinstance(soc, bool) or not isinstance(soc, (int, float)) or not isfinite(soc) or not 0 <= soc <= 100:
            result[device] = 'idle_alarm'
        elif x.senec in {'charge', 'idle'} and x.grid == 'export' and x.pv_surplus:
            result[device] = 'charge_candidate'
        elif x.senec == 'idle' and x.grid == 'import' and soc > 12:
            result[device] = 'discharge_candidate'
    return result


def decide_checked(ownership: str, x: Inputs, quality_report) -> dict[int, str]:
    """Offline composition: failing quality vetoes even claimed verified input.

    A ready report is not proof of calibrated sign, physical surplus or
    stable controller ownership; these remain separate acceptance gates.
    """
    if getattr(quality_report, 'ready', False) is not True:
        owned = OWNERSHIP.get(ownership)
        return {device: 'idle_alarm' if owned is None or device in owned else 'unmanaged'
                for device in (1, 2)}
    return decide(ownership, x)


def audit(ownership: str, senec: str, expected: Mapping[int, str],
          observed: Mapping[int, str], *, data_ok: bool) -> dict[int, str]:
    """Offline anomaly intents; no stop command, attribution or physical proof.

    `observed` is an independently validated per-device battery-power
    direction classification, never inferred from an old zero or a setpoint.
    Battery-side power is not a direct meter of AC exchange or proof of
    physical AC standby. No device I/O occurs.
    """
    owned = OWNERSHIP.get(ownership)
    result = {}
    for device in (1, 2):
        direction = observed.get(device)
        intent = expected.get(device)
        bad = (data_ok is not True or senec not in {'idle', 'charge', 'discharge'}
               or direction not in {'idle', 'charge', 'discharge'})
        if owned is None or device in owned:
            if bad or intent == 'idle_alarm' or intent not in {'idle', 'charge_candidate', 'discharge_candidate'}:
                result[device] = 'stop_intent_alarm'
            elif direction == 'idle':
                result[device] = 'none'
            elif ((direction == 'charge' and intent != 'charge_candidate')
                  or (direction == 'discharge' and intent != 'discharge_candidate')):
                result[device] = 'stop_intent_alarm'
            else:
                result[device] = 'none'
        else:
            counterflow = ((senec == 'discharge' and direction == 'charge')
                           or (senec == 'charge' and direction == 'discharge'))
            result[device] = 'warn_manual' if bad or counterflow else 'none'
    return result


def transition(state: str, event: str, *, device: int | None = None,
               approval: bool = False, healthy: bool = False,
               maintenance: bool = False) -> tuple[str, str]:
    """Compute authority only; never send a device command."""
    if state not in OWNERSHIP:
        return 'manual', 'alarm_invalid_ownership'
    members = OWNERSHIP[state]
    if event == 'maintenance_on':
        return 'manual', 'block_gate_and_notify'
    if event == 'maintenance_off':
        return (state, 'vendor_handover_requires_readback') if not members else (state, 'block_vendor_handover_and_alarm')
    if event == 'restart':
        return state, 'inhibit_writes_until_revalidated'
    if event == 'manual':
        return BY_MEMBERS[members - {device}] if device in (1, 2) else 'manual', 'notify'
    if event == 'grant':
        if device not in (1, 2) or not approval or not healthy or maintenance:
            return state, 'block'
        return BY_MEMBERS[members | {device}], 'handover_requires_readback'
    return state, 'block_unknown_event'
