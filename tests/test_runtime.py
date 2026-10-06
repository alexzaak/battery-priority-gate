"""Controller cycle regression tests using HA-shaped states and service API."""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from offline_package import prepare
prepare()
from custom_components.senec_marstek_gate.authority import Authority
from custom_components.senec_marstek_gate.controller import Evidence, Limits
from custom_components.senec_marstek_gate.quality import REQUIRED
from custom_components.senec_marstek_gate.runtime import GateRuntime, ControllerBinding

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
                             lambda eid, watts: float(hass.states.get(eid).state) == watts,
                             lambda device: True)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
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
