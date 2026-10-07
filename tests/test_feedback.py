"""HA-shaped, no-device tests for fresh setpoint and independent AC feedback."""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from offline_package import prepare
prepare()
from custom_components.senec_marstek_gate.feedback import HAFeedback, FeedbackTimeout, FeedbackInvalid

NOW = datetime(2026, 10, 7, 8, tzinfo=timezone.utc)

class States:
    def __init__(self): self.values = {}
    def get(self, entity_id): return self.values.get(entity_id)
    def put(self, eid, value, ts, *, ac=False, unit='W'):
        self.values[eid] = SimpleNamespace(entity_id=eid, state=str(value),
            last_reported=ts, attributes={'unit_of_measurement':unit,
            **({'device_class':'power','state_class':'measurement'} if ac else {})})

class FeedbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.states = States()
        self.feedback = HAFeedback(self.states, timeout_s=.3, poll_s=.001,
                                   ac_max_abs_w=20, ac_sample_gap_s=.001,
                                   clock=lambda: NOW+timedelta(seconds=3))
    async def test_old_equal_setpoint_is_not_readback(self):
        eid='number.marstek_venus_1_set_charge_power'
        self.states.put(eid,0,NOW)
        with self.assertRaises(FeedbackTimeout):
            await self.feedback.readback(eid,0,NOW,lambda:True)
    async def test_new_setpoint_confirmation_is_ha_state_not_physical_proof(self):
        eid='number.marstek_venus_1_set_charge_power'
        self.states.put(eid,0,NOW+timedelta(seconds=1))
        self.assertTrue(await self.feedback.readback(eid,0,NOW,lambda:True))
    async def test_wrong_unit_bad_value_or_wrong_entity_fails(self):
        eid='number.marstek_venus_1_set_charge_power'
        for value, unit in [('nan','W'),(0,'kW')]:
            self.states.put(eid,value,NOW+timedelta(seconds=1),unit=unit)
            with self.assertRaises(FeedbackTimeout):
                await self.feedback.readback(eid,0,NOW,lambda:True)
        self.states.put(eid,0,NOW+timedelta(seconds=1))
        self.states.values[eid].entity_id='number.other'
        with self.assertRaises(FeedbackTimeout):
            await self.feedback.readback(eid,0,NOW,lambda:True)
    async def test_stale_ac_zero_cannot_pass(self):
        self.states.put('sensor.marstek_venus_1_ac_power',0,NOW,ac=True)
        with self.assertRaises(FeedbackTimeout):
            await self.feedback.ac_idle(1,NOW,lambda:True)
    async def test_two_distinct_new_ac_reports_required(self):
        eid='sensor.marstek_venus_1_ac_power'
        self.states.put(eid,0,NOW+timedelta(seconds=1),ac=True)
        with self.assertRaises(FeedbackTimeout):
            await self.feedback.ac_idle(1,NOW,lambda:True)
        async def second_sample():
            await asyncio.sleep(.005)
            self.states.put(eid,8,NOW+timedelta(seconds=2),ac=True)
        task=asyncio.create_task(second_sample())
        self.assertTrue(await self.feedback.ac_idle(1,NOW,lambda:True))
        await task
    async def test_ac_must_be_finite_bounded_and_metadata_valid(self):
        eid='sensor.marstek_venus_2_ac_power'
        for val, unit in [(float('nan'),'W'),(40,'W'),(0,'kW')]:
            with self.subTest(val=val,unit=unit):
                self.states.put(eid,val,NOW+timedelta(seconds=1),ac=True,unit=unit)
                with self.assertRaises(FeedbackTimeout):
                    await self.feedback.ac_idle(2,NOW,lambda:True)
    async def test_guard_revocation_aborts_poll_immediately(self):
        calls=0
        def guard():
            nonlocal calls
            calls+=1
            return calls<3
        with self.assertRaises(FeedbackInvalid):
            await self.feedback.readback('number.marstek_venus_1_set_charge_power',0,NOW,guard)
    async def test_reject_invalid_configuration_and_device(self):
        for args in [(0,.01,20,1),(.1,0,20,1),(.1,.01,-1,1),(.1,.01,20,-1)]:
            with self.assertRaises(ValueError):HAFeedback(self.states, timeout_s=args[0],poll_s=args[1],ac_max_abs_w=args[2],ac_sample_gap_s=args[3])
        with self.assertRaises(FeedbackInvalid):
            await self.feedback.ac_idle(3,NOW,lambda:True)

if __name__=='__main__':unittest.main()
