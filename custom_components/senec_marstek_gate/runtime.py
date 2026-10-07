"""HA event-loop controller. A normal config entry has NO control binding.

A binding is only usable after independent topology, source, device heartbeat,
AC-stop, writer-origin and user-consent validation. No HA option currently
constructs one: simply installing or reloading cannot enable battery writes.
"""
import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from datetime import datetime
import logging
from typing import Callable, Mapping, Awaitable

from .authority import Authority, advance
from .controller import CycleState, Evidence, Limits, evaluate
from .quality import REQUIRED, assess
from .writer import apply_request, WriteBlocked, ReadbackFailed
from .handover import inspect_handover, classify_pool, is_pool_permissible
from .handover_protocol import HandoverCoordinator, HandoverDenied

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
    handover_authorize: Callable[[object, str], bool] | None = None
    handover_eligible: Callable[[], bool] | None = None
    stop_and_attest: Callable[[], Awaitable[bool]] | None = None
    handover_fault: Callable[[str], None] | None = None


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
        self.handover = None
        handover_authorize = activation.handover_authorize if activation is not None else None
        handover_eligible = activation.handover_eligible if activation is not None else None
        stop_and_attest = activation.stop_and_attest if activation is not None else None
        handover_fault = activation.handover_fault if activation is not None else None
        if (callable(handover_authorize) and callable(handover_eligible) and
                callable(stop_and_attest) and callable(handover_fault)):
            self.handover = HandoverCoordinator(hass,
                authorize=handover_authorize,
                eligible=handover_eligible,
                zero_and_attest=stop_and_attest,
                revoke=self._revoke_handover, grant=self._grant_handover,
                on_fault=handover_fault,
                timeout_s=25, poll_s=.2)

    def _revoke_handover(self):
        self.authority = Authority()
        self.cycle = CycleState()

    def _grant_handover(self):
        # Only the transaction calls this, after both pool and stop acceptance.
        self.authority = Authority('gate_beide', 'enfluri', False)
        self.cycle = CycleState()

    def _invalidate_lease(self):
        if self.handover is not None:
            self.handover.invalidate()

    async def async_takeover(self, permit):
        if self._closed or self.handover is None:
            raise HandoverDenied('no certified HA handover binding installed')
        async with self._lock:
            if self._closed or self.handover is None:
                raise HandoverDenied('handover binding unloaded while waiting')
            return await self.handover.takeover(permit)

    async def async_return(self, permit):
        if self._closed or self.handover is None:
            raise HandoverDenied('no certified HA handover binding installed')
        async with self._lock:
            if self._closed or self.handover is None:
                raise HandoverDenied('handover binding unloaded while waiting')
            # Coordinator validates the one-use permit under its own lock and
            # revokes gate rights before its first stop or switch operation.
            return await self.handover.return_to_auto(permit)

    def close(self):
        """Inhibit in-flight writes before canceling subscription on unload."""
        self._closed = True
        if self.handover is not None:
            self.handover.invalidate()
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
            # Event snapshots may describe a transient loss already restored in
            # hass.states. A loss seen in either view must remain latched.
            incoming_states=SimpleNamespace(get=lambda requested:
                current if requested==eid else self.hass.states.get(requested))
            lost=(not inspect_handover(incoming_states).handover_observed or
                  not inspect_handover(self.hass.states).handover_observed)
            if lost:
                handover=self.handover
                automation_lost=(eid=='automation.marstek_wartung_beide_manuell_und_0_w' and
                                 (state!='on' or self._flag(eid)!='on'))
                switches=(self._flag('switch.marstek_venus_1_battery_manual_mode'),
                          self._flag('switch.marstek_venus_2_battery_manual_mode'))
                phase=getattr(handover,'phase','unowned') if handover is not None else 'unowned'
                stop_in_progress=bool(getattr(handover,'stop_in_progress',False)) if handover is not None else False
                pool_invalid=(eid=='sensor.omnibattery_integration_status' and
                              (not is_pool_permissible(phase, switches, classify_pool(current),
                                                       stop_in_progress=stop_in_progress) or
                               not is_pool_permissible(phase, switches, classify_pool(self._state(eid)),
                                                       stop_in_progress=stop_in_progress)))
                should_invalidate=(handover is not None and
                                   (handover.phase=='held' or handover.stop_in_progress or
                                    (handover.phase in ('transferring','releasing') and
                                     (automation_lost or pool_invalid))))
                if should_invalidate:
                    self._invalidate_lease()
                    self.authority = advance(self.authority, 'restart')
                    self.cycle = CycleState()
                    self.last_reason = 'pool_handover_lost'
                elif handover is None or handover.phase not in ('transferring','releasing'):
                    self.authority = advance(self.authority, 'restart')
                    self.cycle = CycleState()
                    self.last_reason = 'pool_handover_lost'
            return
        if eid == 'input_boolean.marstek_wartung_beide_manuell':
            if state == 'on':
                self._invalidate_lease()
                self.authority = advance(self.authority, 'maintenance_on')
                self.cycle = CycleState()
                self.last_reason = 'maintenance'
            elif state != 'off':
                self._invalidate_lease()
                self.authority = Authority()
                self.cycle = CycleState()
                self.last_reason = 'unknown_interlock'
            return
        for device in (1, 2):
            if eid == f'input_boolean.marstek_gate_venus_{device}_manueller_vorrang':
                if state == 'on':
                    self._invalidate_lease()
                    self.authority = advance(self.authority, 'manual_helper_on', device=device)
                    self.cycle = CycleState()
                    self.last_reason = 'manual_override'
                elif state != 'off':
                    self._invalidate_lease()
                    self.authority = Authority()
                    self.cycle = CycleState()
                    self.last_reason = 'unknown_interlock'
                return
            if eid == f'switch.marstek_venus_{device}_battery_manual_mode' and state != 'on':
                if self.handover is None or self.handover.phase!='releasing':
                    self._invalidate_lease()
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
            self._invalidate_lease()
            self.authority = Authority()
            self.cycle = CycleState()
            return 'unknown_interlock'
        if maintenance == 'on':
            self._invalidate_lease()
            self.authority = advance(self.authority, 'maintenance_on')
            self.cycle = CycleState()
            return 'maintenance'
        if self.authority.maintenance:
            self.authority = advance(self.authority, 'maintenance_off')
            self.cycle = CycleState()
        overridden = [i for i in (1, 2) if manual[i] == 'on']
        if overridden:
            self._invalidate_lease()
            for i in overridden:
                self.authority = advance(self.authority, 'manual_helper_on', device=i)
            self.cycle = CycleState()
            return 'manual_override'
        owned = {'gate_venus_1': (1,), 'gate_venus_2': (2,), 'gate_beide': (1, 2)}.get(self.authority.ownership, ())
        for i in owned:
            if self._flag(f'switch.marstek_venus_{i}_battery_manual_mode') != 'on':
                self._invalidate_lease()
                self.authority = Authority()
                self.cycle = CycleState()
                return 'manual_switch_unknown_or_off'
        if owned:
            handover = inspect_handover(self.hass.states)
            if not handover.handover_observed:
                self._invalidate_lease()
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
            self._invalidate_lease()
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
                self._invalidate_lease()
                self.authority = advance(self.authority, 'restart')
                self.cycle = CycleState()
                self.last_reason = 'interlock_or_readback_failure'
            return self.last_reason
