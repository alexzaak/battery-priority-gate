"""Read-only, strict two-device Omnibattery handover checks."""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from offline_package import prepare
prepare()
from custom_components.senec_marstek_gate.handover import inspect_handover, classify_pool, is_pool_permissible

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

    def test_classify_pool_matrix(self):
        def pool(auto, man, state='charging', eid='sensor.omnibattery_integration_status'):
            return SimpleNamespace(entity_id=eid, state=state,
                                    attributes={'automatic_batteries': auto, 'manual_batteries': man})
        self.assertEqual(classify_pool(pool(['Marstek Venus 1', 'Marstek Venus 2'], [])), 'both_auto')
        self.assertEqual(classify_pool(pool(['Marstek Venus 2'], ['Marstek Venus 1'])), 'partial_takeover')
        self.assertEqual(classify_pool(pool(['Marstek Venus 1'], ['Marstek Venus 2'])), 'partial_return')
        self.assertEqual(classify_pool(pool([], ['Marstek Venus 1', 'Marstek Venus 2'])), 'both_manual')
        # Unclear cases
        self.assertEqual(classify_pool(None), 'unclear')
        self.assertEqual(classify_pool(pool([], [], state='unavailable')), 'unclear')
        self.assertEqual(classify_pool(pool([], [], state='unknown')), 'unclear')
        self.assertEqual(classify_pool(pool([], [], eid='other_sensor')), 'unclear')
        self.assertEqual(classify_pool(pool(['Marstek Venus 1', 'Marstek Venus 1'], [])), 'unclear')
        self.assertEqual(classify_pool(pool(['Marstek Venus 1'], ['Marstek Venus 1'])), 'unclear')
        self.assertEqual(classify_pool(pool(['Marstek Venus 1'], [])), 'unclear')
        self.assertEqual(classify_pool(SimpleNamespace(entity_id='sensor.omnibattery_integration_status', state='charging', attributes='invalid')), 'unclear')

    def test_is_pool_permissible_matrix(self):
        # Stop in progress requires ('on', 'on') and both_manual
        self.assertTrue(is_pool_permissible('transferring', ('on', 'on'), 'both_manual', stop_in_progress=True))
        self.assertFalse(is_pool_permissible('transferring', ('on', 'on'), 'partial_takeover', stop_in_progress=True))
        self.assertFalse(is_pool_permissible('held', ('on', 'on'), 'both_auto', stop_in_progress=True))

        # Held requires ('on', 'on') and both_manual
        self.assertTrue(is_pool_permissible('held', ('on', 'on'), 'both_manual'))
        self.assertFalse(is_pool_permissible('held', ('on', 'on'), 'partial_takeover'))
        self.assertFalse(is_pool_permissible('held', ('on', 'off'), 'both_manual'))

        # Transferring phase
        self.assertTrue(is_pool_permissible('transferring', ('off', 'off'), 'both_auto'))
        self.assertFalse(is_pool_permissible('transferring', ('off', 'off'), 'both_manual'))
        self.assertTrue(is_pool_permissible('transferring', ('on', 'off'), 'partial_takeover'))
        self.assertFalse(is_pool_permissible('transferring', ('on', 'off'), 'both_auto'))  # Reversion blocked!
        self.assertFalse(is_pool_permissible('transferring', ('on', 'off'), 'both_manual'))
        self.assertTrue(is_pool_permissible('transferring', ('on', 'on'), 'partial_takeover'))
        self.assertTrue(is_pool_permissible('transferring', ('on', 'on'), 'both_manual'))
        self.assertFalse(is_pool_permissible('transferring', ('on', 'on'), 'both_auto'))
        self.assertFalse(is_pool_permissible('transferring', ('off', 'on'), 'partial_takeover'))

        # Releasing phase
        self.assertTrue(is_pool_permissible('releasing', ('on', 'on'), 'both_manual'))
        self.assertFalse(is_pool_permissible('releasing', ('on', 'on'), 'both_auto'))
        self.assertTrue(is_pool_permissible('releasing', ('off', 'on'), 'partial_return'))
        self.assertFalse(is_pool_permissible('releasing', ('off', 'on'), 'both_manual'))  # Reversion blocked!
        self.assertFalse(is_pool_permissible('releasing', ('off', 'on'), 'both_auto'))
        self.assertTrue(is_pool_permissible('releasing', ('off', 'off'), 'partial_return'))
        self.assertTrue(is_pool_permissible('releasing', ('off', 'off'), 'both_auto'))
        self.assertFalse(is_pool_permissible('releasing', ('off', 'off'), 'both_manual'))
        self.assertFalse(is_pool_permissible('releasing', ('on', 'off'), 'partial_return'))

        # Unclear always rejected
        self.assertFalse(is_pool_permissible('transferring', ('on', 'off'), 'unclear'))
        self.assertFalse(is_pool_permissible('held', ('on', 'on'), 'unclear'))

if __name__ == '__main__':
    unittest.main()
