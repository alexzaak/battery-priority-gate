"""Offline-only tests: writer is deliberately not registered with HA."""
import unittest
import asyncio
from types import SimpleNamespace
from datetime import datetime, timezone
from typing import Mapping, cast
from offline_package import prepare
prepare()
from custom_components.senec_marstek_gate.authority import Authority
from custom_components.senec_marstek_gate.controller import Evidence, Limits
from custom_components.senec_marstek_gate.writer import apply_request, WriteBlocked, ReadbackFailed
from custom_components.senec_marstek_gate.feedback import HAFeedback, FeedbackTimeout

LIMITS = Limits(100, 80, 50, {1: 300, 2: 300}, {1: 300, 2: 300}, 180, 14)
AUTH = Authority('gate_venus_1', 'enfluri', False)
PROOF = Evidence(True, True, {1: True, 2: False}, False, {1: True, 2: False})


class FakeHass:
    def __init__(self):
        self.values = {f'number.marstek_venus_1_set_{direction}_power': 0
                       for direction in ('charge', 'discharge')}
        self.calls = []
        self.services = self

    async def async_call(self, domain, service, data, blocking=False):
        self.calls.append((domain, service, dict(data), blocking))
        self.values[data['entity_id']] = data['value']


class WriterTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_feedback_blocks_old_ac_zero_then_allows_two_post_zero_reports(self):
        hass = FakeHass()
        states = {}
        hass.states = SimpleNamespace(get=states.get)
        now = lambda: datetime.now(timezone.utc)
        def put(eid, value, *, ac=False):
            states[eid] = SimpleNamespace(entity_id=eid, state=str(value), last_reported=now(),
                attributes={'unit_of_measurement':'W',
                            **({'device_class':'power','state_class':'measurement'} if ac else {})})
        ac_eid = 'sensor.marstek_venus_1_ac_power'
        put(ac_eid,0,ac=True)  # old and equal is NOT acceptable
        original = hass.async_call
        async def service(domain, service, data, blocking=False):
            await original(domain,service,data,blocking)
            put(data['entity_id'],data['value'])
        hass.services = SimpleNamespace(async_call=service)
        feedback = HAFeedback(hass.states,timeout_s=.3,poll_s=.001,
                              ac_max_abs_w=20,ac_sample_gap_s=.002)
        with self.assertRaises(FeedbackTimeout):
            await apply_request(hass,1,('charge_intent',200),AUTH,PROOF,LIMITS,
                                lambda:True,feedback.readback,feedback.ac_idle)
        self.assertEqual([c[2]['value'] for c in hass.calls],[0,0])
        hass.calls.clear()
        async def report_ac():
            await asyncio.sleep(.005)
            put(ac_eid,4,ac=True)
            await asyncio.sleep(.006)
            put(ac_eid,6,ac=True)
        task = asyncio.create_task(report_ac())
        await apply_request(hass,1,('charge_intent',200),AUTH,PROOF,LIMITS,
                            lambda:True,feedback.readback,feedback.ac_idle)
        await task
        self.assertEqual([c[2]['value'] for c in hass.calls],[0,0,200])

    async def test_async_feedback_gets_post_command_timestamp_and_guard(self):
        hass = FakeHass()
        readbacks, idle = [], []
        async def readback(entity, value, issued_at, guard):
            readbacks.append((entity,value,issued_at))
            return guard() and hass.values[entity] == value
        async def ac_idle(device, after, guard):
            idle.append((device,after))
            return guard()
        await apply_request(hass, 1, ('charge_intent', 250), AUTH, PROOF,
                            LIMITS, lambda: True, readback, ac_idle)
        self.assertEqual(len(readbacks),3)
        self.assertEqual(len(idle),1)
        self.assertTrue(all(isinstance(ts,datetime) and ts.tzinfo == timezone.utc for _,_,ts in readbacks))
        self.assertGreater(idle[0][1], readbacks[1][2])

    async def test_charge_requires_stop_readback_before_nonzero(self):
        hass = FakeHass()
        result = await apply_request(hass, 1, ('charge_intent', 250), AUTH, PROOF,
                                     LIMITS, guard=lambda: True,
                                     readback=lambda entity, value, after, guard: hass.values[entity] == value,
                                     ac_idle=lambda device, after, guard: True)
        self.assertEqual(result, 'charge_intent')
        self.assertEqual([(c[2]['entity_id'], c[2]['value']) for c in hass.calls],
                         [('number.marstek_venus_1_set_charge_power', 0),
                          ('number.marstek_venus_1_set_discharge_power', 0),
                          ('number.marstek_venus_1_set_charge_power', 250)])
        self.assertTrue(all(c[3] is True and c[:2] == ('number', 'set_value') for c in hass.calls))

    async def test_zero_requires_both_setpoints_and_ac_idle(self):
        hass = FakeHass()
        self.assertEqual(await apply_request(hass, 1, ('zero_intent', 0), AUTH, PROOF,
                         LIMITS, lambda: True, lambda e, v, after, guard: hass.values[e] == v,
                         lambda device, after, guard: True), 'zero_intent')
        self.assertEqual(len(hass.calls), 2)

    async def test_no_write_without_proven_exclusive_ownership(self):
        for authority, proof in ((Authority(), PROOF), (AUTH, Evidence(True, True, {1: False}, False, {1: True})),
                                 (Authority('gate_venus_1', 'enfluri', True), PROOF),
                                 (Authority('gate_venus_1', 'enfluri', False, maintenance=True), PROOF),
                                 (AUTH, Evidence(True, True, cast(Mapping[int, bool], None), False,
                                                 cast(Mapping[int, bool], None)))):
            with self.subTest(authority=authority, proof=proof):
                hass = FakeHass()
                with self.assertRaises(WriteBlocked):
                    await apply_request(hass, 1, ('zero_intent', 0), authority, proof,
                                        LIMITS, lambda: True, lambda e, v, after, guard: True, lambda d, after, guard: True)
                self.assertEqual(hass.calls, [])

    async def test_guard_loss_aborts_before_following_write(self):
        hass = FakeHass()
        checks = iter((True, False))
        with self.assertRaises(WriteBlocked):
            await apply_request(hass, 1, ('charge_intent', 200), AUTH, PROOF, LIMITS,
                                lambda: next(checks), lambda e, v, after, guard: True, lambda d, after, guard: True)
        self.assertEqual(len(hass.calls), 1)

    async def test_missing_readback_or_idle_never_reaches_nonzero(self):
        for readback, idle in ((lambda e, v, after, guard: False, lambda d, after, guard: True),
                               (lambda e, v, after, guard: True, lambda d, after, guard: False)):
            hass = FakeHass()
            with self.assertRaises(ReadbackFailed):
                await apply_request(hass, 1, ('discharge_intent', 200), AUTH, PROOF,
                                    LIMITS, lambda: True, readback, idle)
            self.assertTrue(all(c[2]['value'] == 0 for c in hass.calls))

    async def test_reject_invalid_power_and_unmanaged_device(self):
        for device, request in ((1, ('charge_intent', float('nan'))),
                                (1, ('discharge_intent', 301)), (2, ('zero_intent', 0)),
                                (1, ('hold', None)), (1, ('charge_intent', -1))):
            hass = FakeHass()
            with self.subTest(device=device, request=request), self.assertRaises(WriteBlocked):
                await apply_request(hass, device, request, AUTH, PROOF, LIMITS,
                                    lambda: True, lambda e, v, after, guard: True, lambda d, after, guard: True)
            self.assertEqual(hass.calls, [])


if __name__ == '__main__':
    unittest.main()
