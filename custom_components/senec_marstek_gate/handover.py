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


def classify_pool(pool_obj) -> str:
    """Classify pool membership of both Venus devices.

    Returns one of:
      'both_auto', 'partial_takeover', 'partial_return', 'both_manual', or 'unclear'
    """
    try:
        if pool_obj is None:
            return 'unclear'
        eid = getattr(pool_obj, 'entity_id', None)
        if eid is not None and eid != 'sensor.omnibattery_integration_status':
            return 'unclear'
        if getattr(pool_obj, 'state', None) in ('unknown', 'unavailable', None):
            return 'unclear'
        attrs = getattr(pool_obj, 'attributes', None)
        if not isinstance(attrs, dict):
            return 'unclear'
        automatic = attrs.get('automatic_batteries')
        manual = attrs.get('manual_batteries')
        if not isinstance(automatic, list) or not isinstance(manual, list):
            return 'unclear'
        if len(automatic) != len(set(automatic)) or len(manual) != len(set(manual)):
            return 'unclear'
        auto, man = set(automatic), set(manual)
        if auto & man or not (auto | man).issuperset(VENUS_NAMES):
            return 'unclear'
        v1_man = 'Marstek Venus 1' in man
        v2_man = 'Marstek Venus 2' in man
        if not v1_man and not v2_man:
            return 'both_auto'
        if v1_man and not v2_man:
            return 'partial_takeover'
        if not v1_man and v2_man:
            return 'partial_return'
        if v1_man and v2_man:
            return 'both_manual'
        return 'unclear'
    except Exception:
        return 'unclear'


def is_pool_permissible(phase: str, switches: tuple[str, str], pool_classification: str,
                        *, stop_in_progress: bool = False) -> bool:
    """Evaluate whether an Omnibattery pool report is valid for the current phase.

    Fails closed on any unexpected, reverted, ambiguous, or out-of-order state.
    """
    if pool_classification == 'unclear':
        return False
    if stop_in_progress:
        return switches == ('on', 'on') and pool_classification == 'both_manual'
    if phase == 'held':
        return switches == ('on', 'on') and pool_classification == 'both_manual'
    if phase == 'transferring':
        if switches == ('off', 'off'):
            return pool_classification == 'both_auto'
        if switches == ('on', 'off'):
            return pool_classification == 'partial_takeover'
        if switches == ('on', 'on'):
            return pool_classification in ('partial_takeover', 'both_manual')
        return False
    if phase == 'releasing':
        if switches == ('on', 'on'):
            return pool_classification == 'both_manual'
        if switches == ('off', 'on'):
            return pool_classification == 'partial_return'
        if switches == ('off', 'off'):
            return pool_classification in ('partial_return', 'both_auto')
        return False
    return True
