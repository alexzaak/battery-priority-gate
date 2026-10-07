"""Bounded HA-state feedback, NOT device acknowledgement or physical certification.

Setpoint state can be optimistic. AC measurements are independent HA entities,
but two HA reports alone do not attest hardware heartbeat or meter topology.
No service calls and no production activation are performed here.
"""
import asyncio
from datetime import datetime, timezone
from math import isfinite

class FeedbackTimeout(RuntimeError):
    """No fresh matching HA report within the finite observation window."""

class FeedbackInvalid(RuntimeError):
    """Invalid device, time or lost safety guard."""

class HAFeedback:
    def __init__(self, states, *, timeout_s: float, poll_s: float,
                 ac_max_abs_w: float, ac_sample_gap_s: float,
                 max_report_age_s: float = 10, clock=None):
        for v in (timeout_s,poll_s,ac_max_abs_w,ac_sample_gap_s,max_report_age_s):
            if type(v) not in (int,float) or not isfinite(v) or v <= 0:
                raise ValueError('feedback bounds must be finite positive numbers')
        self.states, self.timeout_s, self.poll_s = states, timeout_s, poll_s
        self.ac_max_abs_w, self.ac_sample_gap_s = ac_max_abs_w, ac_sample_gap_s
        self.max_report_age_s = max_report_age_s
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _sample(self, eid, *, after, ac):
        try:
            obj = self.states.get(eid)
            if obj is None or obj.entity_id != eid:
                return None
            stamp = obj.last_reported
            now = self.clock()
            if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp <= after:
                return None
            if not isinstance(now,datetime) or now.tzinfo is None or not 0 <= (now-stamp).total_seconds() <= self.max_report_age_s:
                return None
            attrs = obj.attributes
            if attrs.get('unit_of_measurement') != 'W':
                return None
            if ac and (attrs.get('device_class') != 'power' or attrs.get('state_class') != 'measurement'):
                return None
            value = float(obj.state)
            if not isfinite(value):
                return None
            return (stamp,value)
        except (AttributeError,TypeError,ValueError,OverflowError):
            return None

    async def _poll(self, get_result, guard):
        deadline = asyncio.get_running_loop().time() + self.timeout_s
        while True:
            if guard() is not True:
                raise FeedbackInvalid('authority lost while awaiting feedback')
            result = get_result()
            if result is not None:
                return result
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise FeedbackTimeout('fresh feedback not observed')
            await asyncio.sleep(min(self.poll_s,remaining))

    async def readback(self, entity_id, watts, after, guard):
        valid_entities = {f'number.marstek_venus_{n}_set_{mode}_power'
                          for n in (1,2) for mode in ('charge','discharge')}
        if not (isinstance(entity_id,str) and entity_id in valid_entities and
                type(watts) in (int,float) and isfinite(watts) and
                isinstance(after,datetime) and after.tzinfo is not None):
            raise FeedbackInvalid('invalid setpoint request')
        def match():
            sample = self._sample(entity_id,after=after,ac=False)
            return True if sample is not None and abs(sample[1]-watts)<1e-6 else None
        return await self._poll(match,guard)

    async def ac_idle(self, device, after, guard):
        if type(device) is not int or device not in (1,2) or not isinstance(after,datetime) or after.tzinfo is None:
            raise FeedbackInvalid('invalid AC verification request')
        eid = f'sensor.marstek_venus_{device}_ac_power'
        first = None
        def match():
            nonlocal first
            sample = self._sample(eid,after=after,ac=True)
            if sample is None or abs(sample[1])>self.ac_max_abs_w:
                first = None
                return None
            if first is None or sample[0] < first:
                first = sample[0]
                return None
            if sample[0] > first and (sample[0]-first).total_seconds() >= self.ac_sample_gap_s:
                return True
            return None
        return await self._poll(match,guard)
