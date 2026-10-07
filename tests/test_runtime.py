"""Controller cycle regression tests using HA-shaped states and service API."""
import asyncio
from dataclasses import replace
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from offline_package import prepare
prepare()
from custom_components.senec_marstek_gate.authority import Authority
from custom_components.senec_marstek_gate.controller import Evidence, Limits
from custom_components.senec_marstek_gate.quality import REQUIRED
from custom_components.senec_marstek_gate.runtime import GateRuntime, ControllerBinding
from custom_components.senec_marstek_gate.handover_protocol import HandoverDenied, HandoverIncomplete

NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
LIMITS = Limits(100, 80, 30, {1: 250, 2: 250}, {1: 250, 2: 250}, 10, 14)


class States:
    def __init__(self, now):
        self.now = now
        self.values = {}
        self.calls = []
        for eid, val in {'sensor.senec_enfluri_net_power_total': -450,
                         'sensor.senec_battery_state_power': 0,
                         'sensor.senec_solar_generated_power': 900,
                         'sensor.marstek_venus_1_battery_power': 0,
                         'sensor.marstek_venus_2_battery_power': 0,
                         'sensor.marstek_venus_1_battery_soc': 60,
                         'sensor.marstek_venus_2_battery_soc': 60}.items():
            unit = REQUIRED[eid]
            self.values[eid] = self.state(eid, str(val), now, {
                'unit_of_measurement': unit,
                'device_class': 'battery' if unit == '%' else 'power',
                'state_class': 'measurement'})
        for eid in ('input_boolean.marstek_wartung_beide_manuell',
                    'input_boolean.marstek_gate_venus_1_manueller_vorrang',
                    'input_boolean.marstek_gate_venus_2_manueller_vorrang'):
            self.values[eid] = self.state(eid, 'off', now)
        for i in (1, 2):
            eid = f'switch.marstek_venus_{i}_battery_manual_mode'
            self.values[eid] = self.state(eid, 'on', now)
        self.values['automation.marstek_wartung_beide_manuell_und_0_w'] = self.state(
            'automation.marstek_wartung_beide_manuell_und_0_w', 'on', now)
        self.values['sensor.omnibattery_integration_status'] = self.state(
            'sensor.omnibattery_integration_status', 'charging', now,
            {'automatic_batteries': [], 'manual_batteries': ['Marstek Venus 1', 'Marstek Venus 2']})

    @staticmethod
    def state(eid, value, now, attrs=None):
        return SimpleNamespace(entity_id=eid, state=value, attributes=attrs or {}, last_reported=now)

    def get(self, eid):
        self.calls.append(eid)
        return self.values.get(eid)

    def advance(self, now):
        self.now = now
        for eid, state in self.values.items():
            if eid in REQUIRED:
                state.last_reported = now


class HA:
    def __init__(self, now):
        self.states = States(now)
        self.services = self
        self.calls = []

    async def async_call(self, domain, service, data, blocking=False):
        assert (domain, service, blocking) == ('number', 'set_value', True)
        self.calls.append((data['entity_id'], data['value']))
        self.states.values[data['entity_id']] = self.states.state(data['entity_id'],
                                                                  str(data['value']), self.states.now)


def binding(hass):
    attested = {eid: {'topology': True, 'sign': True, 'heartbeat': True} for eid in REQUIRED}
    proof = Evidence(True, True, {1: True, 2: False}, False, {1: True, 2: False})
    return ControllerBinding(LIMITS, {eid: 8 for eid in REQUIRED}, attested,
                             lambda: proof, lambda device: True,
                             lambda eid, watts, after, guard: float(hass.states.get(eid).state) == watts,
                             lambda device, after, guard: True)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_external_pool_loss_invalidates_held_handover_lease(self):
        hass=HA(NOW)
        runtime=GateRuntime(hass,binding(hass),Authority('gate_beide','enfluri',False))
        revoked=[]
        runtime.handover=SimpleNamespace(phase='held',invalidate=lambda:revoked.append(True))
        pool=hass.states.values['sensor.omnibattery_integration_status']
        pool.attributes['automatic_batteries']=['Marstek Venus 1']
        runtime.handle_state_change(SimpleNamespace(data={'entity_id':pool.entity_id,'new_state':pool}))
        self.assertEqual(revoked,[True])
        self.assertTrue(runtime.authority.inhibited)

    async def test_manual_override_invalidates_held_handover_lease(self):
        hass=HA(NOW)
        runtime=GateRuntime(hass,binding(hass),Authority('gate_beide','enfluri',False))
        revoked=[]
        runtime.handover=SimpleNamespace(phase='held',invalidate=lambda:revoked.append(True))
        eid='input_boolean.marstek_gate_venus_1_manueller_vorrang'
        manual=hass.states.values[eid];manual.state='on'
        runtime.handle_state_change(SimpleNamespace(data={'entity_id':eid,'new_state':manual}))
        self.assertEqual(revoked,[True])
        self.assertFalse(runtime._live_guard(1))

    async def test_delayed_maintenance_automation_off_event_invalidates_held_lease(self):
        hass=HA(NOW)
        runtime=GateRuntime(hass,binding(hass),Authority('gate_beide','enfluri',False))
        revoked=[]
        runtime.handover=SimpleNamespace(phase='held',invalidate=lambda:revoked.append(True))
        eid='automation.marstek_wartung_beide_manuell_und_0_w'
        # hass.states still reports on; the event itself reports a prior off.
        runtime.handle_state_change(SimpleNamespace(data={'entity_id':eid,
            'new_state':hass.states.state(eid,'off',NOW)}))
        self.assertEqual(revoked,[True])
        self.assertTrue(runtime.authority.inhibited)

    async def test_runtime_joint_handover_requires_explicit_bound_callbacks(self):
        hass=HA(NOW)
        runtime=GateRuntime(hass,binding(hass))
        with self.assertRaises(HandoverDenied):
            await runtime.async_takeover(SimpleNamespace(request_id='x', action='takeover', devices=(1,2)))
        self.assertEqual(hass.calls,[])

    async def test_queued_takeover_cannot_grant_after_unload(self):
        hass=HA(NOW)
        for n in (1,2):
            hass.states.values[f'switch.marstek_venus_{n}_battery_manual_mode'].state='off'
        pool=hass.states.values['sensor.omnibattery_integration_status'].attributes
        pool['automatic_batteries']=['Marstek Venus 1','Marstek Venus 2']
        pool['manual_batteries']=[]
        async def switch_service(domain,service,data,blocking=False):
            hass.calls.append((domain,service,data['entity_id']))
        hass.services=SimpleNamespace(async_call=switch_service)
        async def zero():return True
        b=replace(binding(hass),handover_authorize=lambda p,action:True,
                  handover_eligible=lambda:True,stop_and_attest=zero,
                  handover_fault=lambda reason:None)
        runtime=GateRuntime(hass,b)
        runtime.handover.timeout_s=.01
        runtime.handover.poll_s=.001
        await runtime._lock.acquire()
        task=asyncio.create_task(runtime.async_takeover(SimpleNamespace(
            request_id='new-request',action='takeover',devices=(1,2))))
        await asyncio.sleep(0)
        runtime.close()
        runtime._lock.release()
        with self.assertRaises(HandoverDenied):await task
        self.assertEqual(hass.calls,[])
        self.assertEqual(runtime.authority.ownership,'manual')

    async def test_queued_return_cannot_call_coordinator_after_unload(self):
        hass=HA(NOW)
        runtime=GateRuntime(hass,binding(hass),Authority('gate_beide','enfluri',False))
        calls=[]
        class FakeHandover:
            def invalidate(self):calls.append('invalidate')
            async def return_to_auto(self,permit):
                calls.append('return')
                return True
        runtime.handover=FakeHandover()
        await runtime._lock.acquire()
        task=asyncio.create_task(runtime.async_return(SimpleNamespace(request_id='return')))
        await asyncio.sleep(0)
        runtime.close()
        runtime._lock.release()
        with self.assertRaises(HandoverDenied):await task
        self.assertEqual(calls,['invalidate'])
        self.assertEqual(hass.calls,[])

    async def test_unapproved_return_does_not_revoke_held_gate_or_consume_lease(self):
        hass=HA(NOW)
        async def zero():return True
        b=replace(binding(hass),handover_authorize=lambda p,action:False,
                  handover_eligible=lambda:True,stop_and_attest=zero,
                  handover_fault=lambda reason:None)
        runtime=GateRuntime(hass,b,Authority('gate_beide','enfluri',False))
        runtime.handover.phase='held'  # synthetic lease; no actors touched
        denied=SimpleNamespace(request_id='unauthorized',action='return',devices=(1,2))
        with self.assertRaises(HandoverDenied):await runtime.async_return(denied)
        self.assertEqual(runtime.handover.phase,'held')
        self.assertEqual(runtime.authority.ownership,'gate_beide')
        self.assertFalse(runtime.authority.inhibited)
        self.assertEqual(runtime.handover.used_requests,set())
        self.assertEqual(hass.calls,[])

    async def test_replayed_return_does_not_revoke_held_gate(self):
        hass=HA(NOW)
        async def zero():return True
        b=replace(binding(hass),handover_authorize=lambda p,action:True,
                  handover_eligible=lambda:True,stop_and_attest=zero,
                  handover_fault=lambda reason:None)
        runtime=GateRuntime(hass,b,Authority('gate_beide','enfluri',False))
        runtime.handover.phase='held'
        runtime.handover.used_requests.add('already-consumed')
        replay=SimpleNamespace(request_id='already-consumed',action='return',devices=(1,2))
        with self.assertRaises(HandoverDenied):await runtime.async_return(replay)
        self.assertEqual(runtime.handover.phase,'held')
        self.assertEqual(runtime.authority.ownership,'gate_beide')
        self.assertFalse(runtime.authority.inhibited)
        self.assertEqual(hass.calls,[])

    async def test_invalid_return_queued_behind_tick_does_not_revoke_live_guard(self):
        hass=HA(NOW)
        async def zero():return True
        b=replace(binding(hass),handover_authorize=lambda p,action:False,
                  handover_eligible=lambda:True,stop_and_attest=zero,
                  handover_fault=lambda reason:None)
        runtime=GateRuntime(hass,b,Authority('gate_beide','enfluri',False))
        runtime.handover.phase='held'
        self.assertEqual(await runtime.async_tick(NOW),'settling')
        hass.states.advance(NOW+timedelta(seconds=10))
        entered=asyncio.Event();release=asyncio.Event()
        original=hass.async_call
        async def blocked_number(domain,service,data,blocking=False):
            entered.set()
            await release.wait()
            await original(domain,service,data,blocking)
        hass.services=SimpleNamespace(async_call=blocked_number)
        tick=asyncio.create_task(runtime.async_tick(hass.states.now))
        try:
            await asyncio.wait_for(entered.wait(),1)
            denied=SimpleNamespace(request_id='invalid-parallel',action='return',devices=(1,2))
            rejection=asyncio.create_task(runtime.async_return(denied))
            await asyncio.sleep(0)
            self.assertFalse(rejection.done())
            self.assertEqual(runtime.authority.ownership,'gate_beide')
            self.assertTrue(runtime._live_guard(1))
        finally:
            release.set()
        await asyncio.wait_for(tick,1)
        with self.assertRaises(HandoverDenied):await rejection
        self.assertEqual(runtime.handover.phase,'held')
        self.assertEqual(runtime.authority.ownership,'gate_beide')
        self.assertFalse(runtime.authority.inhibited)

    async def test_transient_pool_loss_during_stop_prevents_grant_even_if_restored(self):
        hass=HA(NOW)
        for n in (1,2):
            hass.states.values[f'switch.marstek_venus_{n}_battery_manual_mode'].state='off'
        pool=hass.states.values['sensor.omnibattery_integration_status']
        pool.attributes['automatic_batteries']=['Marstek Venus 1','Marstek Venus 2']
        pool.attributes['manual_batteries']=[]
        original=hass.async_call
        async def switch_service(domain,service,data,blocking=False):
            if domain=='switch':
                n=int(data['entity_id'].split('_venus_')[1].split('_')[0]);name=f'Marstek Venus {n}'
                hass.states.values[data['entity_id']].state='on'
                pool.attributes['automatic_batteries'].remove(name)
                pool.attributes['manual_batteries'].append(name)
                hass.calls.append((data['entity_id'],service))
            else:await original(domain,service,data,blocking)
        hass.services=SimpleNamespace(async_call=switch_service)
        async def zero():
            pool.attributes['manual_batteries'].remove('Marstek Venus 2')
            pool.attributes['automatic_batteries'].append('Marstek Venus 2')
            runtime.handle_state_change(SimpleNamespace(data={
                'entity_id':pool.entity_id,'new_state':pool}))
            pool.attributes['automatic_batteries'].remove('Marstek Venus 2')
            pool.attributes['manual_batteries'].append('Marstek Venus 2')
            return True
        b=replace(binding(hass),handover_authorize=lambda p,action:True,
                  handover_eligible=lambda:True,stop_and_attest=zero,
                  handover_fault=lambda reason:None)
        runtime=GateRuntime(hass,b)
        with self.assertRaises(HandoverIncomplete):
            await runtime.async_takeover(SimpleNamespace(
                request_id='transient-loss',action='takeover',devices=(1,2)))
        self.assertEqual(runtime.handover.phase,'recovery_required')
        self.assertEqual(runtime.authority.ownership,'manual')
        self.assertTrue(runtime.authority.inhibited)
        self.assertEqual(len(hass.calls),2)

    async def test_delayed_pool_loss_event_during_stop_blocks_grant(self):
        hass=HA(NOW)
        for n in (1,2):
            hass.states.values[f'switch.marstek_venus_{n}_battery_manual_mode'].state='off'
        pool=hass.states.values['sensor.omnibattery_integration_status']
        pool.attributes['automatic_batteries']=['Marstek Venus 1','Marstek Venus 2']
        pool.attributes['manual_batteries']=[]
        async def switch_service(domain,service,data,blocking=False):
            n=int(data['entity_id'].split('_venus_')[1].split('_')[0]);name=f'Marstek Venus {n}'
            hass.states.values[data['entity_id']].state='on'
            pool.attributes['automatic_batteries'].remove(name)
            pool.attributes['manual_batteries'].append(name)
            hass.calls.append((data['entity_id'],service))
        hass.services=SimpleNamespace(async_call=switch_service)
        async def zero():
            past_pool=hass.states.state(pool.entity_id,'charging',NOW,{
                'automatic_batteries':['Marstek Venus 2'],
                'manual_batteries':['Marstek Venus 1']})
            # HA current state already recovered, but event conveys the loss.
            runtime.handle_state_change(SimpleNamespace(data={
                'entity_id':pool.entity_id,'new_state':past_pool}))
            return True
        b=replace(binding(hass),handover_authorize=lambda p,action:True,
                  handover_eligible=lambda:True,stop_and_attest=zero,
                  handover_fault=lambda reason:None)
        runtime=GateRuntime(hass,b)
        with self.assertRaises(HandoverIncomplete):
            await runtime.async_takeover(SimpleNamespace(
                request_id='delayed-loss',action='takeover',devices=(1,2)))
        self.assertEqual(runtime.handover.phase,'recovery_required')
        self.assertEqual(runtime.authority.ownership,'manual')

    async def test_delayed_automation_loss_during_transfer_blocks_second_switch(self):
        hass=HA(NOW)
        for n in (1,2):
            hass.states.values[f'switch.marstek_venus_{n}_battery_manual_mode'].state='off'
        pool=hass.states.values['sensor.omnibattery_integration_status']
        pool.attributes['automatic_batteries']=['Marstek Venus 1','Marstek Venus 2']
        pool.attributes['manual_batteries']=[]
        async def switch_service(domain,service,data,blocking=False):
            n=int(data['entity_id'].split('_venus_')[1].split('_')[0]);name=f'Marstek Venus {n}'
            hass.states.values[data['entity_id']].state='on'
            pool.attributes['automatic_batteries'].remove(name)
            pool.attributes['manual_batteries'].append(name)
            hass.calls.append((data['entity_id'],service))
            if n==1:
                eid='automation.marstek_wartung_beide_manuell_und_0_w'
                runtime.handle_state_change(SimpleNamespace(data={'entity_id':eid,
                    'new_state':hass.states.state(eid,'off',NOW)}))
        hass.services=SimpleNamespace(async_call=switch_service)
        async def zero():return True
        b=replace(binding(hass),handover_authorize=lambda p,action:True,
                  handover_eligible=lambda:True,stop_and_attest=zero,
                  handover_fault=lambda reason:None)
        runtime=GateRuntime(hass,b)
        with self.assertRaises(HandoverIncomplete):
            await runtime.async_takeover(SimpleNamespace(
                request_id='early-automation-loss',action='takeover',devices=(1,2)))
        self.assertEqual(runtime.handover.phase,'recovery_required')
        self.assertEqual(runtime.authority.ownership,'manual')
        self.assertEqual(len(hass.calls),1)

    async def test_runtime_grant_only_after_joint_stop_and_explicit_return(self):
        hass=HA(NOW)
        for n in (1,2):
            hass.states.values[f'switch.marstek_venus_{n}_battery_manual_mode'].state='off'
        pool=hass.states.values['sensor.omnibattery_integration_status'].attributes
        pool['automatic_batteries']=['Marstek Venus 1','Marstek Venus 2'];pool['manual_batteries']=[]
        originals=hass.async_call
        async def switch_service(domain,service,data,blocking=False):
            if domain=='switch':
                self.assertTrue(blocking)
                if service=='turn_off':
                    self.assertEqual(runtime.authority.ownership,'manual')
                    self.assertTrue(runtime.authority.inhibited)
                n=int(data['entity_id'].split('_venus_')[1].split('_')[0]);name=f'Marstek Venus {n}'
                hass.states.values[data['entity_id']].state='on' if service=='turn_on' else 'off'
                (pool['manual_batteries'] if service=='turn_on' else pool['automatic_batteries']).append(name)
                (pool['automatic_batteries'] if service=='turn_on' else pool['manual_batteries']).remove(name)
                hass.calls.append((data['entity_id'],service))
            else:await originals(domain,service,data,blocking)
        hass.services=SimpleNamespace(async_call=switch_service)
        approved=SimpleNamespace(request_id='approval-2',action='takeover',devices=(1,2))
        async def zero():
            self.assertEqual(runtime.authority.ownership,'manual')
            self.assertTrue(runtime.authority.inhibited)
            return True  # synthetic physical proof, never production binding
        b=replace(binding(hass),handover_authorize=lambda p,action:p.request_id in ('approval-2','approval-3') and p.action==action and p.devices==(1,2),
                  handover_eligible=lambda:True, stop_and_attest=zero,
                  handover_fault=lambda reason:None)
        runtime=GateRuntime(hass,b)
        self.assertTrue(await runtime.async_takeover(approved))
        self.assertEqual(runtime.authority.ownership,'gate_beide')
        self.assertFalse(runtime.authority.inhibited)
        self.assertTrue(await runtime.async_return(SimpleNamespace(request_id='approval-3',action='return',devices=(1,2))))
        self.assertTrue(runtime.authority.inhibited)
        self.assertEqual(runtime.authority.ownership,'manual')
        self.assertEqual(pool['automatic_batteries'],['Marstek Venus 1','Marstek Venus 2'])

    async def test_default_ha_runtime_never_reads_or_writes(self):
        hass = HA(NOW)
        runtime = GateRuntime(hass)
        self.assertEqual(await runtime.async_tick(NOW), 'inactive')
        self.assertEqual(hass.states.calls, [])
        self.assertEqual(hass.calls, [])

    async def test_authorized_cycle_calls_real_service_sequence_after_stability(self):
        hass = HA(NOW)
        runtime = GateRuntime(hass, binding(hass), Authority('gate_venus_1', 'enfluri', False))
        self.assertEqual(await runtime.async_tick(NOW), 'settling')
        self.assertEqual(hass.calls, [])
        hass.states.advance(NOW + timedelta(seconds=10))
        self.assertEqual(await runtime.async_tick(hass.states.now), 'candidate_not_authorization')
        self.assertEqual(hass.calls, [('number.marstek_venus_1_set_charge_power', 0),
                                      ('number.marstek_venus_1_set_discharge_power', 0),
                                      ('number.marstek_venus_1_set_charge_power', 250)])

    async def test_manual_on_revokes_and_off_never_regrants(self):
        hass = HA(NOW)
        runtime = GateRuntime(hass, binding(hass), Authority('gate_venus_1', 'enfluri', False))
        manual = hass.states.values['input_boolean.marstek_gate_venus_1_manueller_vorrang']
        manual.state = 'on'
        self.assertEqual(await runtime.async_tick(NOW), 'manual_override')
        self.assertEqual(runtime.authority.ownership, 'manual')
        manual.state = 'off'
        self.assertEqual(await runtime.async_tick(NOW + timedelta(seconds=1)), 'manual')
        self.assertEqual(hass.calls, [])

    async def test_manual_on_event_revokes_immediately_before_next_tick(self):
        hass = HA(NOW)
        runtime = GateRuntime(hass, binding(hass), Authority('gate_venus_1', 'enfluri', False))
        runtime.handle_state_change(SimpleNamespace(data={
            'entity_id': 'input_boolean.marstek_gate_venus_1_manueller_vorrang',
            'new_state': hass.states.state('input_boolean.marstek_gate_venus_1_manueller_vorrang', 'on', NOW)}))
        self.assertEqual(runtime.authority.ownership, 'manual')
        self.assertFalse(runtime._live_guard(1))
        self.assertEqual(hass.calls, [])

    async def test_unknown_manual_state_blocks_all_writes(self):
        hass = HA(NOW)
        runtime = GateRuntime(hass, binding(hass), Authority('gate_beide', 'enfluri', False))
        hass.states.values['input_boolean.marstek_gate_venus_2_manueller_vorrang'].state = 'unknown'
        self.assertEqual(await runtime.async_tick(NOW), 'unknown_interlock')
        self.assertEqual(runtime.authority.ownership, 'manual')
        self.assertEqual(hass.calls, [])

    async def test_missing_source_cannot_issue_positive_setpoint(self):
        hass = HA(NOW)
        runtime = GateRuntime(hass, binding(hass), Authority('gate_venus_1', 'enfluri', False))
        hass.states.values.pop('sensor.senec_enfluri_net_power_total')
        self.assertEqual(await runtime.async_tick(NOW), 'invalid_quality_or_limits')
        self.assertEqual(hass.calls, [('number.marstek_venus_1_set_charge_power', 0),
                                      ('number.marstek_venus_1_set_discharge_power', 0)])

    async def test_manual_takeover_between_zero_and_positive_blocks_write(self):
        hass = HA(NOW)
        runtime = GateRuntime(hass, binding(hass), Authority('gate_venus_1', 'enfluri', False))
        await runtime.async_tick(NOW)
        hass.states.advance(NOW + timedelta(seconds=10))
        original = hass.async_call
        async def takes_over(domain, service, data, blocking=False):
            await original(domain, service, data, blocking)
            if len(hass.calls) == 2:
                manual = hass.states.values['input_boolean.marstek_gate_venus_1_manueller_vorrang']
                manual.state = 'on'
                runtime.handle_state_change(SimpleNamespace(data={
                    'entity_id': manual.entity_id, 'new_state': manual}))
        hass.services = SimpleNamespace(async_call=takes_over)
        self.assertEqual(await runtime.async_tick(hass.states.now), 'interlock_or_readback_failure')
        self.assertEqual(runtime.authority.ownership, 'manual')
        self.assertTrue(all(value == 0 for _, value in hass.calls))

    async def test_service_failure_inhibits_authority_and_does_not_retry(self):
        hass = HA(NOW)
        runtime = GateRuntime(hass, binding(hass), Authority('gate_venus_1', 'enfluri', False))
        await runtime.async_tick(NOW)
        hass.states.advance(NOW + timedelta(seconds=10))
        async def fails(*args, **kwargs):
            raise RuntimeError('device disconnected')
        hass.services = SimpleNamespace(async_call=fails)
        self.assertEqual(await runtime.async_tick(hass.states.now), 'interlock_or_readback_failure')
        self.assertTrue(runtime.authority.inhibited)
        self.assertEqual(await runtime.async_tick(hass.states.now + timedelta(seconds=1)), 'inhibited')

    async def test_pool_loss_event_revokes_without_waiting_for_tick(self):
        hass = HA(NOW)
        runtime = GateRuntime(hass, binding(hass), Authority('gate_beide', 'enfluri', False))
        status = hass.states.values['sensor.omnibattery_integration_status']
        status.attributes['automatic_batteries'] = ['Marstek Venus 1']
        runtime.handle_state_change(SimpleNamespace(data={'entity_id': status.entity_id, 'new_state': status}))
        self.assertTrue(runtime.authority.inhibited)
        self.assertEqual(runtime.last_reason, 'pool_handover_lost')
        self.assertFalse(runtime._live_guard(1))
        self.assertEqual(hass.calls, [])

    async def test_pool_mismatch_before_cycle_blocks_writes(self):
        hass = HA(NOW)
        runtime = GateRuntime(hass, binding(hass), Authority('gate_beide', 'enfluri', False))
        hass.states.values['sensor.omnibattery_integration_status'].attributes['automatic_batteries'] = ['Marstek Venus 1']
        self.assertEqual(await runtime.async_tick(NOW), 'pool_ambiguous')
        self.assertTrue(runtime.authority.inhibited)
        self.assertEqual(hass.calls, [])

    async def test_pool_loss_between_zero_and_positive_blocks_write(self):
        hass = HA(NOW)
        runtime = GateRuntime(hass, binding(hass), Authority('gate_venus_1', 'enfluri', False))
        await runtime.async_tick(NOW)
        hass.states.advance(NOW + timedelta(seconds=10))
        original = hass.async_call
        async def loses_pool(domain, service, data, blocking=False):
            await original(domain, service, data, blocking)
            if len(hass.calls) == 2:
                hass.states.values['sensor.omnibattery_integration_status'].attributes['automatic_batteries'] = ['Marstek Venus 1']
        hass.services = SimpleNamespace(async_call=loses_pool)
        self.assertEqual(await runtime.async_tick(hass.states.now), 'interlock_or_readback_failure')
        self.assertTrue(all(v == 0 for _, v in hass.calls))

    async def test_unverified_source_or_external_guard_blocks(self):
        hass = HA(NOW)
        b = binding(hass)
        b = ControllerBinding(b.limits, b.max_age_s, {}, b.evidence, b.write_guard,
                              b.readback, b.ac_idle)
        runtime = GateRuntime(hass, b, Authority('gate_venus_1', 'enfluri', False))
        await runtime.async_tick(NOW)
        self.assertTrue(all(value == 0 for _, value in hass.calls))


if __name__ == '__main__':
    unittest.main()
