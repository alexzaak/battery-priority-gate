"""Read-only, strict two-device Omnibattery handover checks."""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from offline_package import prepare
prepare()
from custom_components.senec_marstek_gate.handover import inspect_handover

NOW = datetime(2026, 10, 7, 6, tzinfo=timezone.utc)

class States:
    def __init__(self):
        self.items = {}
        for name, value in {
            'input_boolean.marstek_wartung_beide_manuell': 'off',
            'input_boolean.marstek_gate_venus_1_manueller_vorrang': 'off',
            'input_boolean.marstek_gate_venus_2_manueller_vorrang': 'off',
            'switch.marstek_venus_1_battery_manual_mode': 'off',
            'switch.marstek_venus_2_battery_manual_mode': 'off',
            'automation.marstek_wartung_beide_manuell_und_0_w': 'on',
        }.items():
            self.items[name] = SimpleNamespace(entity_id=name, state=value, attributes={}, last_reported=NOW)
        self.items['sensor.omnibattery_integration_status'] = SimpleNamespace(
            entity_id='sensor.omnibattery_integration_status', state='charging',
            attributes={'automatic_batteries': ['Marstek Venus 1', 'Marstek Venus 2'], 'manual_batteries': []},
            last_reported=NOW)
    def get(self, eid):
        return self.items.get(eid)

class HandoverTests(unittest.TestCase):
    def setUp(self):
        self.states = States()
    def check(self):
        return inspect_handover(self.states)
    def manual(self):
        for i in (1, 2):
            self.states.items[f'switch.marstek_venus_{i}_battery_manual_mode'].state = 'on'
        a = self.states.items['sensor.omnibattery_integration_status'].attributes
        a['automatic_batteries'] = []
        a['manual_batteries'] = ['Marstek Venus 1', 'Marstek Venus 2']
    def test_automatic_pool_is_not_gate_authority(self):
        r = self.check()
        self.assertEqual(r.reason, 'automatic_pool')
        self.assertFalse(r.handover_observed)
        self.assertFalse(r.exclusive_writer_proven)
    def test_both_manual_in_omnibattery_still_not_exclusive_proof(self):
        self.manual()
        r = self.check()
        self.assertEqual(r.reason, 'both_manual_pool_confirmed_only')
        self.assertTrue(r.handover_observed)
        self.assertFalse(r.exclusive_writer_proven)
    def test_partial_or_ambiguous_membership_fails_closed(self):
        self.manual()
        for auto, manual in [(['Marstek Venus 1'], ['Marstek Venus 1', 'Marstek Venus 2']),
                             ([], ['Marstek Venus 1']),
                             ([], ['Marstek Venus 1', 'Marstek Venus 1']),
                             (None, ['Marstek Venus 1', 'Marstek Venus 2'])]:
            with self.subTest(auto=auto, manual=manual):
                a = self.states.items['sensor.omnibattery_integration_status'].attributes
                a['automatic_batteries'] = auto
                a['manual_batteries'] = manual
                self.assertFalse(self.check().handover_observed)
    def test_interlock_unknown_on_or_missing_blocks(self):
        self.manual()
        for eid in ('input_boolean.marstek_wartung_beide_manuell',
                    'input_boolean.marstek_gate_venus_1_manueller_vorrang',
                    'automation.marstek_wartung_beide_manuell_und_0_w'):
            with self.subTest(eid=eid):
                old = self.states.items[eid].state
                self.states.items[eid].state = 'unavailable'
                self.assertFalse(self.check().handover_observed)
                self.states.items[eid].state = old
        self.states.items['input_boolean.marstek_gate_venus_2_manueller_vorrang'].state = 'on'
        self.assertFalse(self.check().handover_observed)
    def test_identity_not_inferred_from_partial_or_missing_switches(self):
        self.manual()
        self.states.items['switch.marstek_venus_1_battery_manual_mode'].state = 'off'
        self.assertFalse(self.check().handover_observed)
        del self.states.items['switch.marstek_venus_1_battery_manual_mode']
        self.assertFalse(self.check().handover_observed)
    def test_manual_pool_does_not_prove_physical_stop_or_no_other_writer(self):
        self.manual()
        self.assertFalse(self.check().exclusive_writer_proven)

if __name__ == '__main__':
    unittest.main()
