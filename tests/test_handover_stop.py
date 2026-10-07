"""Transactional two-Venus stop tests with fake HA states/services."""
import asyncio
import unittest
from datetime import datetime,timezone
from types import SimpleNamespace
from offline_package import prepare
prepare()
from custom_components.senec_marstek_gate.handover_stop import TwoDeviceZeroAttestor, StopBlocked
from custom_components.senec_marstek_gate.feedback import HAFeedback, FeedbackTimeout

class HA:
    def __init__(self):
        self.values={};self.states=SimpleNamespace(get=self.values.get);self.services=self;self.calls=[]
        self.allow=True;self.pool='both_manual_pool_confirmed_only';self.report_ac=False
    async def async_call(self,domain,service,data,blocking=False):
        assert (domain,service,blocking)==('number','set_value',True)
        self.calls.append((data['entity_id'],data['value']))
        self.put(data['entity_id'],data['value'])
    def put(self,eid,value,ac=False):
        self.values[eid]=SimpleNamespace(entity_id=eid,state=str(value),last_reported=datetime.now(timezone.utc),
          attributes={'unit_of_measurement':'W',**({'device_class':'power','state_class':'measurement'} if ac else {})})

class StopTests(unittest.IsolatedAsyncioTestCase):
    def setup_adapter(self):
        ha=HA();feedback=HAFeedback(ha.states,timeout_s=.3,poll_s=.001,ac_max_abs_w=20,ac_sample_gap_s=.002)
        stop=TwoDeviceZeroAttestor(ha,feedback,sole_writer_guard=lambda:ha.allow,
             handover_observed=lambda:ha.pool=='both_manual_pool_confirmed_only',
             max_joint_skew_s=1)
        return ha,stop
    async def test_no_command_when_both_devices_not_in_manual_pool_or_guard_denies(self):
        ha,stop=self.setup_adapter()
        ha.pool='automatic_pool'
        with self.assertRaises(StopBlocked):await stop()
        ha.pool='both_manual_pool_confirmed_only';ha.allow=False
        with self.assertRaises(StopBlocked):await stop()
        self.assertEqual(ha.calls,[])
    async def test_four_zero_readbacks_but_old_ac_zero_cannot_attest_stop(self):
        ha,stop=self.setup_adapter()
        for n in (1,2):ha.put(f'sensor.marstek_venus_{n}_ac_power',0,ac=True)
        with self.assertRaises(FeedbackTimeout):await stop()
        self.assertEqual(ha.calls,[(f'number.marstek_venus_{n}_set_{mode}_power',0)
                                   for n in (1,2) for mode in ('charge','discharge')])
    async def test_both_fresh_ac_reports_required(self):
        ha,stop=self.setup_adapter()
        async def report():
            await asyncio.sleep(.005)
            for n in (1,2):ha.put(f'sensor.marstek_venus_{n}_ac_power',5,ac=True)
            await asyncio.sleep(.006)
            for n in (1,2):ha.put(f'sensor.marstek_venus_{n}_ac_power',6,ac=True)
        task=asyncio.create_task(report())
        self.assertTrue(await stop())
        await task
        self.assertEqual(len(ha.calls),4)
        self.assertTrue(all(value==0 for _,value in ha.calls))
    async def test_first_device_resumes_output_before_second_confirms(self):
        ha,stop=self.setup_adapter()
        async def staggered():
            await asyncio.sleep(.004)
            ha.put('sensor.marstek_venus_1_ac_power',0,ac=True)
            await asyncio.sleep(.004)
            ha.put('sensor.marstek_venus_1_ac_power',0,ac=True)
            await asyncio.sleep(.004)
            ha.put('sensor.marstek_venus_1_ac_power',250,ac=True)
            ha.put('sensor.marstek_venus_2_ac_power',0,ac=True)
            await asyncio.sleep(.004)
            ha.put('sensor.marstek_venus_2_ac_power',0,ac=True)
        task=asyncio.create_task(staggered())
        with self.assertRaises(StopBlocked):await stop()
        await task
    async def test_first_device_resumes_after_both_dwell_confirmations(self):
        ha,stop=self.setup_adapter()
        original=stop.feedback.ac_idle
        async def resumes_at_second_confirmation(device,after,guard):
            result=await original(device,after,guard)
            if device==2:
                ha.put('sensor.marstek_venus_1_ac_power',250,ac=True)
            return result
        stop.feedback.ac_idle=resumes_at_second_confirmation
        async def reports():
            await asyncio.sleep(.004)
            for n in (1,2):ha.put(f'sensor.marstek_venus_{n}_ac_power',0,ac=True)
            await asyncio.sleep(.004)
            for n in (1,2):ha.put(f'sensor.marstek_venus_{n}_ac_power',0,ac=True)
        task=asyncio.create_task(reports())
        with self.assertRaises(StopBlocked):await stop()
        await task
        self.assertEqual(len(ha.calls),4)

    async def test_loss_of_exclusive_guard_after_first_zero_aborts(self):
        ha,stop=self.setup_adapter()
        original=ha.async_call
        async def loses_guard(*a,**kw):
            await original(*a,**kw)
            ha.allow=False
        ha.services=SimpleNamespace(async_call=loses_guard)
        with self.assertRaises(StopBlocked):await stop()
        self.assertEqual(len(ha.calls),1)

if __name__=='__main__':unittest.main()
