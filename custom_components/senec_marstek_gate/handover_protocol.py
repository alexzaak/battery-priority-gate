"""Two-device Omnibattery handover and return transaction (not registered).

This module contains real HA switch service calls. A normal config entry never
creates or invokes it. Callbacks must be backed by independent authenticated
approval, sole-writer proof and physical 0-W/AC acceptance before registration.
A pool list and both manual switches alone NEVER establish those facts.
"""
import asyncio
from collections.abc import Callable
from math import isfinite

from .handover import inspect_handover

_SWITCH = {n: f'switch.marstek_venus_{n}_battery_manual_mode' for n in (1,2)}

class HandoverDenied(RuntimeError):
    """Authorization, preflight or previous ownership absent; no handover."""

class HandoverIncomplete(RuntimeError):
    """Transition failed; recovery may require human intervention."""

class SwitchUnconfirmed(HandoverIncomplete):
    """Switch call exceeded its deadline; the device may still have changed."""

class HandoverCoordinator:
    """Volatile joint lease: no grant after reboot and no one-device pilot."""
    def __init__(self, hass, *, authorize: Callable, eligible: Callable,
                 zero_and_attest: Callable, revoke: Callable, grant: Callable,
                 on_fault: Callable,
                 timeout_s: float, poll_s: float):
        if (any(not callable(x) for x in (authorize,eligible,zero_and_attest,revoke,grant,on_fault)) or
                any(type(x) not in (int,float) or not isfinite(x) or x<=0 for x in (timeout_s,poll_s))):
            raise ValueError('authenticated and bounded handover callbacks required')
        self.hass=hass
        self.authorize=authorize
        self.eligible=eligible
        self.zero_and_attest=zero_and_attest
        self.revoke=revoke
        self.grant=grant
        self.on_fault=on_fault
        self.timeout_s=timeout_s
        self.poll_s=poll_s
        self.phase='unowned'
        self.used_requests=set()
        self._cancelled=False
        self.stop_in_progress=False
        self._lock=asyncio.Lock()

    def invalidate(self):
        """Drop a held lease on user takeover, conflict, unload or fault."""
        if self.phase in ('held','transferring','releasing'):
            self._cancelled=True
            self.revoke()
            self._fault('handover_invalidated')

    def _fault(self, reason):
        self.phase='recovery_required'
        try:self.on_fault(reason)
        except Exception:pass  # still fail closed if notification transport fails

    def _interlocks(self):
        try:
            states=self.hass.states
            for eid in ('input_boolean.marstek_wartung_beide_manuell',
                        'input_boolean.marstek_gate_venus_1_manueller_vorrang',
                        'input_boolean.marstek_gate_venus_2_manueller_vorrang'):
                s=states.get(eid)
                if s is None or s.entity_id!=eid or s.state!='off':return False
            eid='automation.marstek_wartung_beide_manuell_und_0_w'
            s=states.get(eid)
            return s is not None and s.entity_id==eid and s.state=='on'
        except Exception:
            return False

    def _eligible(self):
        try:return not self._cancelled and self._interlocks() and self.eligible() is True
        except Exception:return False

    def _authorized(self, permit, action):
        try:
            request_id=permit.request_id
            if (type(request_id) is not str or not request_id or
                    permit.action!=action or permit.devices!=(1,2) or
                    request_id in self.used_requests or
                    self.authorize(permit,action) is not True):
                return False
            self.used_requests.add(request_id)
            return True
        except Exception:return False

    async def _switch(self, n, service):
        if not self._eligible():raise HandoverIncomplete('lost preflight before switch')
        # Do not await cancellation cleanup of a driver that might ignore it:
        # an unacknowledged service can still have acted on the device.
        task=asyncio.create_task(self.hass.services.async_call(
            'switch',service,{'entity_id':_SWITCH[n]},blocking=True))
        try:
            done,_=await asyncio.wait({task},timeout=self.timeout_s)
            if not done:
                task.cancel()
                task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
                raise SwitchUnconfirmed('switch service deadline exceeded; outcome unknown')
            try:
                await task
            except Exception as exc:
                # A service exception is not proof the device stayed unchanged.
                raise SwitchUnconfirmed('switch service failed; outcome unknown') from exc
        except asyncio.CancelledError:
            task.cancel()
            task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
            raise
        if self._cancelled:raise HandoverIncomplete('handover revoked during service call')

    def _partial_pool(self, *, first_manual):
        """Require an exact, unambiguous joint observation between switch calls."""
        try:
            one=self.hass.states.get(_SWITCH[1])
            two=self.hass.states.get(_SWITCH[2])
            pool=self.hass.states.get('sensor.omnibattery_integration_status')
            if (one is None or one.entity_id!=_SWITCH[1] or two is None or
                    two.entity_id!=_SWITCH[2] or pool is None or
                    pool.entity_id!='sensor.omnibattery_integration_status' or
                    pool.state in ('unknown','unavailable',None)):
                return False
            auto=pool.attributes['automatic_batteries']
            manual=pool.attributes['manual_batteries']
            if first_manual:
                return (one.state=='on' and two.state=='off' and
                        manual==['Marstek Venus 1'] and auto==['Marstek Venus 2'])
            return (one.state=='off' and two.state=='on' and
                    auto==['Marstek Venus 1'] and manual==['Marstek Venus 2'])
        except (AttributeError, KeyError, TypeError):
            return False

    async def _await_observation(self, expected):
        deadline=asyncio.get_running_loop().time()+self.timeout_s
        while True:
            if not self._eligible():raise HandoverIncomplete('interlock or external eligibility lost')
            if inspect_handover(self.hass.states).reason==expected:return
            remaining=deadline-asyncio.get_running_loop().time()
            if remaining<=0:raise HandoverIncomplete('pool confirmation timed out')
            await asyncio.sleep(min(remaining,self.poll_s))

    async def _stop(self):
        if not self._eligible():raise HandoverIncomplete('no exclusive preflight before stop')
        self.stop_in_progress=True
        try:
            task=asyncio.create_task(self.zero_and_attest())
            try:
                done,_=await asyncio.wait({task},timeout=self.timeout_s)
                if not done:
                    self._cancelled=True  # never accept a late stop acknowledgement
                    task.cancel()
                    task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
                    raise HandoverIncomplete('two-device stop deadline exceeded')
                if await task is not True:
                    raise HandoverIncomplete('two-device physical stop not attested')
            except asyncio.CancelledError:
                task.cancel()
                task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
                raise
            if not self._eligible():raise HandoverIncomplete('eligibility lost after stop')
        finally:
            self.stop_in_progress=False

    async def takeover(self, permit):
        async with self._lock:
            if self.phase!='unowned' or not self._authorized(permit,'takeover') or not self._eligible():
                raise HandoverDenied('takeover not approved or preflight absent')
            if inspect_handover(self.hass.states).reason!='automatic_pool':
                raise HandoverDenied('both devices not in Omnibattery automatic pool')
            self.revoke()  # before the first switch call
            self.phase='transferring'
            stop_started=False
            try:
                for n in (1,2):
                    if n==2 and not self._partial_pool(first_manual=True):
                        raise HandoverIncomplete('joint pool changed during takeover')
                    await self._switch(n,'turn_on')
                await self._await_observation('both_manual_pool_confirmed_only')
                stop_started=True
                await self._stop()
                if inspect_handover(self.hass.states).reason!='both_manual_pool_confirmed_only':
                    raise HandoverIncomplete('pool changed after stop')
                self.grant()  # only after full joint transition AND independent stop callback
                self.phase='held'
                return True
            except asyncio.CancelledError:
                # A cancelled HA call may already have changed the device.
                # Never issue compensating switches or grant from this state.
                self.revoke()
                self._fault('takeover_cancelled')
                raise
            except SwitchUnconfirmed as exc:
                self.revoke()
                self._fault('switch_unconfirmed')
                raise HandoverIncomplete('switch outcome unknown; manual recovery required') from exc
            except Exception as exc:
                self.revoke()
                # After a service attempt, pool states can lag or disagree with
                # physical modes. Never compensate an ambiguous partial transfer.
                self._fault('stop_unconfirmed' if stop_started else 'takeover_unconfirmed')
                raise HandoverIncomplete('takeover unconfirmed; manual recovery required') from exc

    async def return_to_auto(self, permit):
        async with self._lock:
            if self.phase!='held' or not self._authorized(permit,'return'):
                raise HandoverDenied('only an owned, expressly approved joint lease can be returned')
            self.revoke()  # gate rights removed before even the first zero/switch
            self.phase='releasing'
            try:
                if inspect_handover(self.hass.states).reason!='both_manual_pool_confirmed_only':
                    raise HandoverIncomplete('manual pool no longer unambiguous')
                await self._stop()
                for n in (1,2):
                    if n==2 and not self._partial_pool(first_manual=False):
                        raise HandoverIncomplete('joint pool changed during return')
                    await self._switch(n,'turn_off')
                await self._await_observation('automatic_pool')
                self.phase='unowned'
                return True
            except asyncio.CancelledError:
                self.revoke()
                self._fault('return_cancelled')
                raise
            except Exception as exc:
                self._fault('return_unconfirmed')
                raise HandoverIncomplete('return unconfirmed; no gate authority') from exc
