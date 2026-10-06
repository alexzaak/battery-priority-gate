"""Isolated HA number writer, NOT registered by the integration.

Its guard/readback/AC-idle callbacks must be bound to *fresh, independent*
production checks before any future wiring. No such binding exists yet.
"""
from math import isfinite
from collections.abc import Mapping

from .authority import Authority
from .controller import Evidence, Limits, _valid_limits
from .gate import OWNERSHIP


class WriteBlocked(RuntimeError):
    """No write authority or invalid request."""


class ReadbackFailed(RuntimeError):
    """Expected setpoint or independently measured AC-idle not confirmed."""


def _check(device, request, authority, evidence, limits):
    if (type(device) is not int or device not in (1, 2) or
            not isinstance(authority, Authority) or
            authority.ownership not in OWNERSHIP or
            device not in OWNERSHIP[authority.ownership] or
            authority.inhibited or authority.maintenance or
            authority.source not in ('enfluri', 'tibber') or
            not isinstance(evidence, Evidence) or evidence.maintenance or
            evidence.source_attested is not True or
            not isinstance(evidence.exclusive, Mapping) or
            not isinstance(evidence.handover_confirmed, Mapping) or
            evidence.exclusive.get(device) is not True or
            evidence.handover_confirmed.get(device) is not True or
            not _valid_limits(limits) or not isinstance(request, tuple) or
            len(request) != 2):
        raise WriteBlocked('no verified write authority')
    kind, watts = request
    if kind == 'zero_intent':
        if type(watts) not in (int, float) or watts != 0:
            raise WriteBlocked('zero intent requires 0 W')
    elif kind in ('charge_intent', 'discharge_intent'):
        cap = limits.max_charge_w[device] if kind == 'charge_intent' else limits.max_discharge_w[device]
        if type(watts) not in (int, float) or not isfinite(watts) or not 0 < watts <= cap:
            raise WriteBlocked('invalid power')
    else:
        raise WriteBlocked('request cannot actuate')
    return kind, watts


async def apply_request(hass, device, request, authority, evidence, limits,
                        guard, readback, ac_idle):
    """Fail closed on any missing proof; never write to SENEC or another device.

    `guard` must check actual manual flags and controller ownership at EACH
    call. `readback` must check fresh real setpoint state; `ac_idle` must check
    independently measured AC output after both zeros, not just setpoints.
    A timeout/error propagates; no unsafe automatic retry or ownership guess.
    """
    kind, watts = _check(device, request, authority, evidence, limits)
    base = f'number.marstek_venus_{device}_set_'
    charge, discharge = base + 'charge_power', base + 'discharge_power'

    async def set_verified(entity, value):
        _check(device, request, authority, evidence, limits)
        if guard() is not True:
            raise WriteBlocked('live ownership guard denied write')
        await hass.services.async_call('number', 'set_value',
                                       {'entity_id': entity, 'value': value}, blocking=True)
        if readback(entity, value) is not True:
            raise ReadbackFailed('setpoint readback missing')

    await set_verified(charge, 0)
    await set_verified(discharge, 0)
    if ac_idle(device) is not True:
        raise ReadbackFailed('independent AC-idle readback missing')
    if kind == 'charge_intent':
        await set_verified(charge, watts)
    elif kind == 'discharge_intent':
        await set_verified(discharge, watts)
    return kind
