"""Two-Venus handover transaction tests. Fake services, never live actors."""
import asyncio
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from offline_package import prepare
prepare()
from custom_components.senec_marstek_gate.handover_protocol import HandoverCoordinator, HandoverDenied, HandoverIncomplete
from custom_components.senec_marstek_gate.handover import inspect_handover

@dataclass(frozen=True)
class Approval:
    request_id: str
    action: str
    devices: tuple = (1,2)

class FakeHA:
    def __init__(self):
        self.states=self
        self.services=self
        self.calls=[]
        self.failed_at=None
        self.delay=False
        self.flags={'input_boolean.marstek_wartung_beide_manuell':'off',
          'input_boolean.marstek_gate_venus_1_manueller_vorrang':'off',
          'input_boolean.marstek_gate_venus_2_manueller_vorrang':'off',
          'automation.marstek_wartung_beide_manuell_und_0_w':'on',
          'switch.marstek_venus_1_battery_manual_mode':'off',
          'switch.marstek_venus_2_battery_manual_mode':'off'}
        self.auto=['Marstek Venus 1','Marstek Venus 2'];self.manual=[]
    def get(self,eid):
        if eid=='sensor.omnibattery_integration_status':
            return SimpleNamespace(entity_id=eid,state='charging',attributes={
                'automatic_batteries':list(self.auto),'manual_batteries':list(self.manual)})
        if eid in self.flags:return SimpleNamespace(entity_id=eid,state=self.flags[eid],attributes={})
        return None
    async def async_call(self,domain,service,data,blocking=False):
        assert domain=='switch' and service in ('turn_on','turn_off') and blocking is True
        eid=data['entity_id'];self.calls.append((service,eid))
        if self.failed_at==len(self.calls):raise RuntimeError('device failed')
        if self.delay:return
        self.flags[eid]='on' if service=='turn_on' else 'off'
        number=int(eid.split('_venus_')[1].split('_')[0]);name=f'Marstek Venus {number}'
        (self.auto if service=='turn_off' else self.manual).append(name)
        (self.manual if service=='turn_off' else self.auto).remove(name)

class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ha=FakeHA();self.events=[];self.eligible=True;self.stop=True;self.authorized=True
        async def zero():self.events.append('zero');return self.stop
        def authorization(permit,action):return self.authorized and permit.action==action and permit.devices==(1,2) and bool(permit.request_id)
        self.protocol=HandoverCoordinator(self.ha,authorize=authorization,
            eligible=lambda:self.eligible,zero_and_attest=zero,
            revoke=lambda:self.events.append('revoke'),
            grant=lambda:self.events.append('grant'),on_fault=lambda reason:self.events.append('fault:'+reason),
            timeout_s=.015,poll_s=.001)
    def permit(self,action):return Approval('approval-takeover' if action=='takeover' else 'approval-return',action)
    async def test_joint_takeover_then_return(self):
        self.assertTrue(await self.protocol.takeover(self.permit('takeover')))
        self.assertEqual(inspect_handover(self.ha.states).reason,'both_manual_pool_confirmed_only')
        self.assertEqual(self.protocol.phase,'held')
        self.assertEqual(self.events,['revoke','zero','grant'])
        self.assertEqual(self.ha.calls[:2],[('turn_on','switch.marstek_venus_1_battery_manual_mode'),('turn_on','switch.marstek_venus_2_battery_manual_mode')])
        self.assertTrue(await self.protocol.return_to_auto(self.permit('return')))
        self.assertEqual(self.protocol.phase,'unowned')
        self.assertEqual(inspect_handover(self.ha.states).reason,'automatic_pool')
        self.assertEqual(self.events,['revoke','zero','grant','revoke','zero'])
        self.assertEqual(self.ha.calls[2:],[('turn_off','switch.marstek_venus_1_battery_manual_mode'),('turn_off','switch.marstek_venus_2_battery_manual_mode')])
    async def test_no_approval_or_preflight_never_switches(self):
        for approval, eligible, authorized in [(Approval('', 'takeover'),True,True),
                                               (self.permit('takeover'),False,True),
                                               (self.permit('takeover'),True,False)]:
            with self.subTest(approval=approval,eligible=eligible):
                self.eligible=eligible;self.authorized=authorized
                with self.assertRaises(HandoverDenied):await self.protocol.takeover(approval)
                self.assertEqual(self.ha.calls,[])
                self.assertEqual(self.protocol.phase,'unowned')
    async def test_partial_switch_failure_rolls_back_without_grant(self):
        self.ha.failed_at=2
        with self.assertRaises(HandoverIncomplete):await self.protocol.takeover(self.permit('takeover'))
        self.assertNotIn('grant',self.events)
        self.assertEqual(self.protocol.phase,'unowned')
        self.assertEqual(inspect_handover(self.ha.states).reason,'automatic_pool')
    async def test_service_mutates_second_switch_then_raises_must_roll_back_both(self):
        original=self.ha.async_call
        async def mutates_then_raises(domain,service,data,blocking=False):
            await original(domain,service,data,blocking)
            if service=='turn_on' and 'venus_2' in data['entity_id']:
                raise RuntimeError('ack lost after device changed')
        self.ha.services=SimpleNamespace(async_call=mutates_then_raises)
        with self.assertRaises(HandoverIncomplete):await self.protocol.takeover(self.permit('takeover'))
        self.assertEqual(self.protocol.phase,'unowned')
        self.assertEqual(inspect_handover(self.ha.states).reason,'automatic_pool')
        self.assertNotIn('grant',self.events)
    async def test_failed_rollback_requires_manual_recovery(self):
        old=self.ha.async_call
        async def fails_return(domain,service,data,blocking=False):
            if service=='turn_off':raise RuntimeError('return failed')
            return await old(domain,service,data,blocking)
        self.ha.services=SimpleNamespace(async_call=fails_return)
        self.ha.failed_at=2
        with self.assertRaises(HandoverIncomplete):await self.protocol.takeover(self.permit('takeover'))
        self.assertEqual(self.protocol.phase,'recovery_required')
        self.assertNotIn('grant',self.events)
        with self.assertRaises(HandoverDenied):await self.protocol.takeover(self.permit('takeover'))
    async def test_zero_failure_leaves_manual_pool_for_explicit_recovery(self):
        self.stop=False
        with self.assertRaises(HandoverIncomplete):await self.protocol.takeover(self.permit('takeover'))
        self.assertEqual(self.protocol.phase,'recovery_required')
        self.assertNotIn('grant',self.events)
        self.assertEqual(inspect_handover(self.ha.states).reason,'both_manual_pool_confirmed_only')
    async def test_manual_takeover_prevents_rollback_actor_calls(self):
        old=self.ha.async_call
        async def manual_while_switching(domain,service,data,blocking=False):
            await old(domain,service,data,blocking)
            if service=='turn_on':self.ha.flags['input_boolean.marstek_gate_venus_1_manueller_vorrang']='on'
        self.ha.services=SimpleNamespace(async_call=manual_while_switching)
        with self.assertRaises(HandoverIncomplete):await self.protocol.takeover(self.permit('takeover'))
        self.assertEqual(self.protocol.phase,'recovery_required')
        self.assertNotIn('grant',self.events)
        self.assertTrue(all(call[0]=='turn_on' for call in self.ha.calls))
    async def test_return_revoke_happens_before_service_and_failed_stop_does_not_return(self):
        await self.protocol.takeover(self.permit('takeover'))
        self.stop=False
        before=len(self.ha.calls)
        with self.assertRaises(HandoverIncomplete):await self.protocol.return_to_auto(self.permit('return'))
        self.assertEqual(self.events[-3:],['revoke','zero','fault:return_unconfirmed'])
        self.assertEqual(len(self.ha.calls),before)
        self.assertEqual(self.protocol.phase,'recovery_required')
    async def test_return_without_lease_cannot_touch_user_manual_modes(self):
        for n in (1,2):self.ha.flags[f'switch.marstek_venus_{n}_battery_manual_mode']='on'
        self.ha.auto=[];self.ha.manual=['Marstek Venus 1','Marstek Venus 2']
        with self.assertRaises(HandoverDenied):await self.protocol.return_to_auto(self.permit('return'))
        self.assertEqual(self.ha.calls,[])
    async def test_pool_stuck_in_transition_times_out_without_grant(self):
        self.ha.delay=True
        with self.assertRaises(HandoverIncomplete):await self.protocol.takeover(self.permit('takeover'))
        self.assertNotIn('grant',self.events)
        self.assertEqual(self.protocol.phase,'unowned')
    async def test_replay_approval_is_rejected(self):
        await self.protocol.takeover(self.permit('takeover'))
        await self.protocol.return_to_auto(self.permit('return'))
        with self.assertRaises(HandoverDenied):await self.protocol.takeover(self.permit('takeover'))
        self.assertEqual(len(self.ha.calls),4)
    async def test_unbounded_stop_callback_times_out_and_cannot_grant(self):
        async def stuck():await asyncio.Future()
        self.protocol.zero_and_attest=stuck
        with self.assertRaises(HandoverIncomplete):await self.protocol.takeover(self.permit('takeover'))
        self.assertNotIn('grant',self.events)
    async def test_cancelled_takeover_after_first_switch_requires_recovery(self):
        started=asyncio.Event()
        old=self.ha.async_call
        async def wait_on_second(domain,service,data,blocking=False):
            if service=='turn_on' and 'venus_2' in data['entity_id']:
                started.set()
                await asyncio.Future()
            return await old(domain,service,data,blocking)
        self.ha.services=SimpleNamespace(async_call=wait_on_second)
        task=asyncio.create_task(self.protocol.takeover(self.permit('takeover')))
        await asyncio.wait_for(started.wait(),1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.protocol.phase,'recovery_required')
        self.assertEqual(self.events[-1],'fault:takeover_cancelled')
        self.assertNotIn('grant',self.events)
        self.assertEqual(self.ha.calls,[('turn_on','switch.marstek_venus_1_battery_manual_mode')])
        self.assertEqual(inspect_handover(self.ha.states).reason,'pool_or_switch_mismatch')

    async def test_cancelled_during_stop_never_rolls_back_to_automatic(self):
        started=asyncio.Event()
        async def stop_waits():
            started.set()
            await asyncio.Future()
        self.protocol.zero_and_attest=stop_waits
        task=asyncio.create_task(self.protocol.takeover(self.permit('takeover')))
        await asyncio.wait_for(started.wait(),1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.protocol.phase,'recovery_required')
        self.assertEqual(self.events[-1],'fault:takeover_cancelled')
        self.assertNotIn('grant',self.events)
        self.assertEqual(len(self.ha.calls),2)
        self.assertEqual(inspect_handover(self.ha.states).reason,'both_manual_pool_confirmed_only')

    async def test_cancelled_return_after_first_switch_requires_recovery(self):
        await self.protocol.takeover(self.permit('takeover'))
        started=asyncio.Event()
        old=self.ha.async_call
        async def wait_on_second(domain,service,data,blocking=False):
            if service=='turn_off' and 'venus_2' in data['entity_id']:
                started.set()
                await asyncio.Future()
            return await old(domain,service,data,blocking)
        self.ha.services=SimpleNamespace(async_call=wait_on_second)
        task=asyncio.create_task(self.protocol.return_to_auto(self.permit('return')))
        await asyncio.wait_for(started.wait(),1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.protocol.phase,'recovery_required')
        self.assertEqual(self.events[-1],'fault:return_cancelled')
        self.assertEqual(self.events.count('revoke'),3)
        self.assertEqual(self.events.count('grant'),1)
        self.assertEqual(len(self.ha.calls),3)
        self.assertEqual(inspect_handover(self.ha.states).reason,'pool_or_switch_mismatch')

    async def test_cancelled_return_during_stop_never_switches_pool(self):
        await self.protocol.takeover(self.permit('takeover'))
        started=asyncio.Event()
        async def stop_waits():
            started.set()
            await asyncio.Future()
        self.protocol.zero_and_attest=stop_waits
        task=asyncio.create_task(self.protocol.return_to_auto(self.permit('return')))
        await asyncio.wait_for(started.wait(),1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.protocol.phase,'recovery_required')
        self.assertEqual(self.events[-1],'fault:return_cancelled')
        self.assertEqual(len(self.ha.calls),2)
        self.assertEqual(inspect_handover(self.ha.states).reason,'both_manual_pool_confirmed_only')

    async def test_manual_invalidation_mid_handover_prevents_second_switch_and_grant(self):
        old=self.ha.async_call
        async def revoke_after_first(domain,service,data,blocking=False):
            await old(domain,service,data,blocking)
            if service=='turn_on':self.protocol.invalidate()
        self.ha.services=SimpleNamespace(async_call=revoke_after_first)
        with self.assertRaises(HandoverIncomplete):await self.protocol.takeover(self.permit('takeover'))
        self.assertEqual(self.protocol.phase,'recovery_required')
        self.assertNotIn('grant',self.events)
        self.assertEqual(len(self.ha.calls),1)

if __name__=='__main__':unittest.main()
