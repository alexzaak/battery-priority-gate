"""Two-device zero setpoint and independent HA AC observation during handover.

Not registered as an HA service. A real `sole_writer_guard` must establish
external provenance/exclusivity; a pool observation alone is NOT sufficient.
HA states, even fresh, are not a physical hardware heartbeat certification.
"""
import asyncio
from datetime import datetime, timezone
from math import isfinite
from .feedback import FeedbackInvalid

class StopBlocked(RuntimeError):
    """Lost transition authority before a safe 0-W command/observation."""

class TwoDeviceZeroAttestor:
    def __init__(self, hass, feedback, *, sole_writer_guard, handover_observed,
                 max_joint_skew_s):
        if (not callable(sole_writer_guard) or not callable(handover_observed) or
                type(max_joint_skew_s) not in (int,float) or not isfinite(max_joint_skew_s) or max_joint_skew_s<=0):
            raise ValueError('external writer and joint pool checks required')
        self.hass=hass
        self.feedback=feedback
        self.sole_writer_guard=sole_writer_guard
        self.handover_observed=handover_observed
        self.max_joint_skew_s=max_joint_skew_s

    def _guard(self):
        try:
            return self.sole_writer_guard() is True and self.handover_observed() is True
        except Exception:
            return False

    async def __call__(self):
        if not self._guard():raise StopBlocked('no verified joint transition authority')
        try:
            for n in (1,2):
                for mode in ('charge','discharge'):
                    if not self._guard():raise StopBlocked('authority lost before zero')
                    eid=f'number.marstek_venus_{n}_set_{mode}_power'
                    issued_at=datetime.now(timezone.utc)
                    await self.hass.services.async_call('number','set_value',
                        {'entity_id':eid,'value':0},blocking=True)
                    await self.feedback.readback(eid,0,issued_at,self._guard)
            after_all_zeros=datetime.now(timezone.utc)
            # Both devices need distinct post-zero AC reports. No positive setpoints.
            results=await asyncio.gather(*(self.feedback.ac_idle(n,after_all_zeros,self._guard)
                                           for n in (1,2)))
            if not (all(x is True for x in results) and self._guard() and
                    self.feedback.joint_ac_idle_snapshot(after_all_zeros,self.max_joint_skew_s)):
                raise StopBlocked('simultaneous two-device AC idle not confirmed')
            return True
        except FeedbackInvalid as exc:
            raise StopBlocked('authority lost while awaiting zero/AC feedback') from exc
