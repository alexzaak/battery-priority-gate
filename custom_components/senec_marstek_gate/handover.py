"""Observe Omnibattery's two-device pool; observation is NOT write authority.

No service calls, no grant, no inference from helper OFF alone. Omnibattery's
status reports its pool, but cannot attest external/app/automation writers.
"""
from dataclasses import dataclass

VENUS_NAMES = ('Marstek Venus 1', 'Marstek Venus 2')

@dataclass(frozen=True)
class HandoverObservation:
    reason: str
    handover_observed: bool
    exclusive_writer_proven: bool = False


def inspect_handover(states) -> HandoverObservation:
    def state(entity_id):
        try:
            obj = states.get(entity_id)
            if obj is None or getattr(obj, 'entity_id', None) != entity_id:
                return None
            return getattr(obj, 'state', None)
        except Exception:
            return None

    if state('input_boolean.marstek_wartung_beide_manuell') != 'off':
        return HandoverObservation('maintenance_or_unknown', False)
    for number in (1, 2):
        if state(f'input_boolean.marstek_gate_venus_{number}_manueller_vorrang') != 'off':
            return HandoverObservation('manual_override_or_unknown', False)
    if state('automation.marstek_wartung_beide_manuell_und_0_w') != 'on':
        return HandoverObservation('maintenance_automation_unknown', False)
    eid = 'sensor.omnibattery_integration_status'
    try:
        obj = states.get(eid)
        if obj is None or getattr(obj, 'entity_id', None) != eid or getattr(obj, 'state', None) in ('unknown', 'unavailable', None):
            return HandoverObservation('omnibattery_unknown', False)
        attrs = getattr(obj, 'attributes', None)
        if not isinstance(attrs, dict):
            return HandoverObservation('pool_unknown', False)
        automatic, manual = attrs['automatic_batteries'], attrs['manual_batteries']
        if not isinstance(automatic, list) or not isinstance(manual, list):
            return HandoverObservation('pool_unknown', False)
        if len(automatic) != len(set(automatic)) or len(manual) != len(set(manual)):
            return HandoverObservation('pool_ambiguous', False)
        auto, man = set(automatic), set(manual)
        if auto & man or not (auto | man).issuperset(VENUS_NAMES):
            return HandoverObservation('pool_ambiguous', False)
    except (KeyError, TypeError, ValueError, AttributeError):
        return HandoverObservation('pool_unknown', False)
    switches = tuple(state(f'switch.marstek_venus_{n}_battery_manual_mode') for n in (1, 2))
    if switches == ('off', 'off') and auto == set(VENUS_NAMES) and not man:
        return HandoverObservation('automatic_pool', False)
    if switches == ('on', 'on') and man == set(VENUS_NAMES) and not auto:
        return HandoverObservation('both_manual_pool_confirmed_only', True)
    return HandoverObservation('pool_or_switch_mismatch', False)
