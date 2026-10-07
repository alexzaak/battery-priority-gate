"""HA event-loop controller. A normal config entry has NO control binding.

A binding is only usable after independent topology, source, device heartbeat,
AC-stop, writer-origin and user-consent validation. No HA option currently
constructs one: simply installing or reloading cannot enable battery writes.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Callable, Mapping, Awaitable

from .authority import Authority, advance
from .controller import CycleState, Evidence, Limits, evaluate
from .quality import REQUIRED, assess
from .writer import apply_request, WriteBlocked, ReadbackFailed
from .handover import inspect_handover

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ControllerBinding:
    """Externally certified signals, never derived from helper OFF or HA setup.

    `write_guard` must freshly verify sole writing controller (incl. Omnibattery
    and maintenance automation), not just an HA switch. `ac_idle` must prove
    actual AC output, not only a zero setpoint or battery-side idle reading.
    """
    limits: Limits
    max_age_s: Mapping[str, float]
    attested: Mapping[str, Mapping[str, bool]]
    evidence: Callable[[], Evidence]
    write_guard: Callable[[int], bool]
    readback: Callable[[str, float, datetime, Callable[[], bool]], bool | Awaitable[bool]]
    ac_idle: Callable[[int, datetime, Callable[[], bool]], bool | Awaitable[bool]]


class GateRuntime:
    """One non-overlapping HA tick per config entry; no read-only shadow mode."""

    def __init__(self, hass, activation: ControllerBinding | None = None,
                 authority: Authority | None = None):
        self.hass = hass
        self.activation = activation
        self.authority = authority if activation is not None and isinstance(authority, Authority) else Authority()
        self.cycle = CycleState()
        self.last_reason = 'inactive'
        self.unsub: Callable[[], None] = lambda: None
        self.unsub_events: Callable[[], None] = lambda: None
        self._lock = asyncio.Lock()
        self._closed = False

    def close(self):
        """Inhibit in-flight writes before canceling subscription on unload."""
        self._closed = True
        self.authority = advance(self.authority, 'restart')
        self.cycle = CycleState()
        self.last_reason = 'unloaded'
        self.unsub()
        self.unsub_events()

    def handle_state_change(self, event):
        """Revoke synchronously on manual/writer handover; OFF is never grant."""
        if self._closed or self.activation is None:
            return
        data = getattr(event, 'data', {})
        if not isinstance(data, Mapping):
            return
        eid = data.get('entity_id')
        current = data.get('new_state')
        state = getattr(current, 'state', None)
        if eid in ('sensor.omnibattery_integration_status',
                   'automation.marstek_wartung_beide_manuell_und_0_w'):
            if not inspect_handover(self.hass.states).handover_observed:
                self.authority = advance(self.authority, 'restart')
                self.cycle = CycleState()
                self.last_reason = 'pool_handover_lost'
            return
        if eid == 'input_boolean.marstek_wartung_beide_manuell':
            if state == 'on':
                self.authority = advance(self.authority, 'maintenance_on')
                self.cycle = CycleState()
                self.last_reason = 'maintenance'
            elif state != 'off':
                self.authority = Authority()
                self.cycle = CycleState()
                self.last_reason = 'unknown_interlock'
            return
        for device in (1, 2):
            if eid == f'input_boolean.marstek_gate_venus_{device}_manueller_vorrang':
                if state == 'on':
                    self.authority = advance(self.authority, 'manual_helper_on', device=device)
                    self.cycle = CycleState()
                    self.last_reason = 'manual_override'
                elif state != 'off':
                    self.authority = Authority()
                    self.cycle = CycleState()
                    self.last_reason = 'unknown_interlock'
                return
            if eid == f'switch.marstek_venus_{device}_battery_manual_mode' and state != 'on':
                self.authority = advance(self.authority, 'manual', device=device)
                self.cycle = CycleState()
                self.last_reason = 'manual_switch_unknown_or_off'
                return

    def _state(self, entity_id: str):
        try:
            s = self.hass.states.get(entity_id)
            return s if s is not None and getattr(s, 'entity_id', None) == entity_id else None
        except Exception:
            return None

    def _flag(self, entity_id: str) -> str:
        s = self._state(entity_id)
        state = getattr(s, 'state', None)
        return state if state in ('on', 'off') else 'unknown'

    def _interlocks(self) -> str:
        maintenance = self._flag('input_boolean.marstek_wartung_beide_manuell')
        manual = {i: self._flag(f'input_boolean.marstek_gate_venus_{i}_manueller_vorrang') for i in (1, 2)}
        if maintenance == 'unknown' or 'unknown' in manual.values():
            self.authority = Authority()
            self.cycle = CycleState()
            return 'unknown_interlock'
        if maintenance == 'on':
            self.authority = advance(self.authority, 'maintenance_on')
            self.cycle = CycleState()
            return 'maintenance'
        if self.authority.maintenance:
            self.authority = advance(self.authority, 'maintenance_off')
            self.cycle = CycleState()
        overridden = [i for i in (1, 2) if manual[i] == 'on']
        if overridden:
            for i in overridden:
                self.authority = advance(self.authority, 'manual_helper_on', device=i)
            self.cycle = CycleState()
            return 'manual_override'
        owned = {'gate_venus_1': (1,), 'gate_venus_2': (2,), 'gate_beide': (1, 2)}.get(self.authority.ownership, ())
        for i in owned:
            if self._flag(f'switch.marstek_venus_{i}_battery_manual_mode') != 'on':
                self.authority = Authority()
                self.cycle = CycleState()
                return 'manual_switch_unknown_or_off'
        if owned:
            handover = inspect_handover(self.hass.states)
            if not handover.handover_observed:
                self.authority = advance(self.authority, 'restart')
                self.cycle = CycleState()
                return handover.reason
        return 'ok'

    def _live_guard(self, device: int) -> bool:
        """Repeated before EACH service call, not a grant from a helper's OFF."""
        if self._closed or self.activation is None or self.authority.inhibited or self.authority.maintenance:
            return False
        owned = {'gate_venus_1': (1,), 'gate_venus_2': (2,), 'gate_beide': (1, 2)}.get(self.authority.ownership, ())
        if device not in owned or self._flag('input_boolean.marstek_wartung_beide_manuell') != 'off':
            return False
        if self._flag(f'input_boolean.marstek_gate_venus_{device}_manueller_vorrang') != 'off':
            return False
        if self._flag(f'switch.marstek_venus_{device}_battery_manual_mode') != 'on':
            return False
        if not inspect_handover(self.hass.states).handover_observed:
            return False
        return self.activation.write_guard(device) is True

    def _quality(self, now: datetime):
        states = {}
        for entity_id in REQUIRED:
            s = self._state(entity_id)
            if s is None:
                continue
            stamp = getattr(s, 'last_reported', None)
            states[entity_id] = {'entity_id': entity_id,
                                 'state': getattr(s, 'state', None),
                                 'attributes': getattr(s, 'attributes', None),
                                 'last_reported': stamp.isoformat() if isinstance(stamp, datetime) else None}
        binding = self.activation
        return assess(states, now=now, max_age_s=binding.max_age_s, attested=binding.attested)

    async def async_tick(self, now: datetime) -> str:
        """Use the actual HA StateMachine; never infer physical proof from it."""
        if self._closed:
            return 'unloaded'
        if self.activation is None:
            return 'inactive'
        if self._lock.locked():
            return 'cycle_busy'
        async with self._lock:
            try:
                interlock = self._interlocks()
                if interlock != 'ok':
                    self.last_reason = interlock
                    return interlock
                if self.authority.inhibited or self.authority.ownership == 'manual':
                    self.cycle = CycleState()
                    self.last_reason = 'inhibited' if self.authority.inhibited else 'manual'
                    return self.last_reason
                if self.authority.source != 'enfluri':
                    self.authority = advance(self.authority, 'restart')
                    self.cycle = CycleState()
                    self.last_reason = 'alternate_source_not_validated'
                    return self.last_reason
                quality = self._quality(now)
                evidence = self.activation.evidence()
                if not isinstance(evidence, Evidence):
                    raise ValueError('invalid evidence')
                self.cycle = CycleState(self.authority.ownership, self.cycle.stable_since,
                                        self.cycle.stable_signature, self.cycle.last_checked)
                outcome = evaluate(self.cycle, quality, evidence, self.activation.limits, now)
                self.cycle = outcome.state
                for device in (1, 2):
                    request = outcome.requests[device]
                    if request[0] not in ('zero_intent', 'charge_intent', 'discharge_intent'):
                        continue
                    await apply_request(self.hass, device, request, self.authority, evidence,
                                        self.activation.limits,
                                        lambda device=device: self._live_guard(device),
                                        self.activation.readback, self.activation.ac_idle)
                    # Re-read grid and provenance on next tick before the second Venus.
                    if request[0] != 'zero_intent':
                        break
                self.last_reason = outcome.reason
            except Exception as exc:
                # Never schedule a retry after any service/reader failure.
                _LOGGER.error('Gate tick inhibited after %s', type(exc).__name__)
                self.authority = advance(self.authority, 'restart')
                self.cycle = CycleState()
                self.last_reason = 'interlock_or_readback_failure'
            return self.last_reason
