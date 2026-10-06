"""Offline-only tests: writer is deliberately not registered with HA."""
import unittest
from typing import Mapping, cast
from offline_package import prepare
prepare()
from custom_components.senec_marstek_gate.authority import Authority
from custom_components.senec_marstek_gate.controller import Evidence, Limits
from custom_components.senec_marstek_gate.writer import apply_request, WriteBlocked, ReadbackFailed

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
    async def test_charge_requires_stop_readback_before_nonzero(self):
        hass = FakeHass()
        result = await apply_request(hass, 1, ('charge_intent', 250), AUTH, PROOF,
                                     LIMITS, guard=lambda: True,
                                     readback=lambda entity, value: hass.values[entity] == value,
                                     ac_idle=lambda device: True)
        self.assertEqual(result, 'charge_intent')
        self.assertEqual([(c[2]['entity_id'], c[2]['value']) for c in hass.calls],
                         [('number.marstek_venus_1_set_charge_power', 0),
                          ('number.marstek_venus_1_set_discharge_power', 0),
                          ('number.marstek_venus_1_set_charge_power', 250)])
        self.assertTrue(all(c[3] is True and c[:2] == ('number', 'set_value') for c in hass.calls))

    async def test_zero_requires_both_setpoints_and_ac_idle(self):
        hass = FakeHass()
        self.assertEqual(await apply_request(hass, 1, ('zero_intent', 0), AUTH, PROOF,
                         LIMITS, lambda: True, lambda e, v: hass.values[e] == v,
                         lambda device: True), 'zero_intent')
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
                                        LIMITS, lambda: True, lambda e, v: True, lambda d: True)
                self.assertEqual(hass.calls, [])

    async def test_guard_loss_aborts_before_following_write(self):
        hass = FakeHass()
        checks = iter((True, False))
        with self.assertRaises(WriteBlocked):
            await apply_request(hass, 1, ('charge_intent', 200), AUTH, PROOF, LIMITS,
                                lambda: next(checks), lambda e, v: True, lambda d: True)
        self.assertEqual(len(hass.calls), 1)

    async def test_missing_readback_or_idle_never_reaches_nonzero(self):
        for readback, idle in ((lambda e, v: False, lambda d: True),
                               (lambda e, v: True, lambda d: False)):
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
                                    lambda: True, lambda e, v: True, lambda d: True)
            self.assertEqual(hass.calls, [])


if __name__ == '__main__':
    unittest.main()
