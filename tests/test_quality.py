"""Data-quality contract for offline HA-shaped snapshots only."""
import unittest
from datetime import datetime, timezone, timedelta
from offline_package import prepare
prepare()
from custom_components.senec_marstek_gate.quality import assess, REQUIRED
from custom_components.senec_marstek_gate.gate import decide_checked, Inputs

NOW = datetime(2026, 10, 5, 18, 0, tzinfo=timezone.utc)


def snapshot(age=2):
    return {entity: {
        'entity_id': entity,
        'state': '50' if entity.endswith('_battery_soc') else '100',
        'attributes': {'unit_of_measurement': '%' if entity.endswith('_battery_soc') else 'W',
                       'device_class': 'battery' if entity.endswith('_battery_soc') else 'power',
                       'state_class': 'measurement'},
        'last_reported': (NOW-timedelta(seconds=age)).isoformat(),
    } for entity in REQUIRED}


def test_config():
    # Explicit synthetic assumptions, NOT validated household calibration.
    return ({entity: 10 for entity in REQUIRED},
            {entity: {'topology': True, 'sign': True, 'heartbeat': True} for entity in REQUIRED})


class QualityTest(unittest.TestCase):
    def test_battery_power_is_required_instead_of_ac_power_for_both_venus(self):
        self.assertIn('sensor.marstek_venus_1_battery_power', REQUIRED)
        self.assertIn('sensor.marstek_venus_2_battery_power', REQUIRED)
        self.assertNotIn('sensor.marstek_venus_1_ac_power', REQUIRED)
        self.assertNotIn('sensor.marstek_venus_2_ac_power', REQUIRED)

    def test_missing_battery_power_is_not_rescued_by_ac_power(self):
        ages, evidence = test_config()
        states = snapshot()
        states.pop('sensor.marstek_venus_2_battery_power')
        states['sensor.marstek_venus_2_ac_power'] = {
            'entity_id': 'sensor.marstek_venus_2_ac_power', 'state': '0',
            'attributes': {'unit_of_measurement': 'W', 'device_class': 'power', 'state_class': 'measurement'},
            'last_reported': NOW.isoformat()}
        report = assess(states, now=NOW, max_age_s=ages, attested=evidence)
        self.assertFalse(report.ready)
        self.assertEqual(report.sources['sensor.marstek_venus_2_battery_power'].reason,
                         'missing_or_mismatched_entity')

    def test_missing_freshness_policy_never_marks_snapshot_ready(self):
        report = assess(snapshot(), now=NOW, max_age_s={}, attested={})
        self.assertFalse(report.ready)
        self.assertTrue(all(not x.ok for x in report.sources.values()))
    def test_valid_synthetic_snapshot_is_ready_only_with_explicit_attestations(self):
        ages, evidence = test_config()
        report = assess(snapshot(), now=NOW, max_age_s=ages, attested=evidence)
        self.assertTrue(report.ready)
        self.assertEqual(report.sources['sensor.senec_battery_state_power'].value, 100.0)
    def test_old_last_reported_blocks_even_with_recent_last_updated(self):
        ages, evidence = test_config()
        states = snapshot()
        key = 'sensor.marstek_venus_2_battery_power'
        states[key]['last_reported'] = (NOW-timedelta(seconds=120)).isoformat()
        states[key]['last_updated'] = NOW.isoformat()
        report = assess(states, now=NOW, max_age_s=ages, attested=evidence)
        self.assertFalse(report.ready)
        self.assertEqual(report.sources[key].reason, 'invalid_or_stale_last_reported')

    def test_missing_and_wrong_units_are_not_zero(self):
        ages, evidence = test_config()
        states = snapshot()
        states.pop('sensor.senec_battery_state_power')
        key = 'sensor.marstek_venus_1_battery_power'
        states[key]['attributes']['unit_of_measurement'] = 'kWh'
        report = assess(states, now=NOW, max_age_s=ages, attested=evidence)
        self.assertIsNone(report.sources['sensor.senec_battery_state_power'].value)
        self.assertEqual(report.sources[key].reason, 'metadata_mismatch')
        self.assertFalse(report.ready)

    def test_missing_heartbeat_attestation_blocks_fresh_constant_value(self):
        ages, evidence = test_config()
        key = 'sensor.senec_enfluri_net_power_total'
        evidence[key]['heartbeat'] = False
        report = assess(snapshot(), now=NOW, max_age_s=ages, attested=evidence)
        self.assertEqual(report.sources[key].reason, 'unvalidated_policy')

    def test_unknown_future_and_out_of_range_values_fail_closed(self):
        ages, evidence = test_config()
        states = snapshot()
        states['sensor.senec_solar_generated_power']['state'] = 'unknown'
        states['sensor.marstek_venus_1_battery_soc']['state'] = '101'
        states['sensor.marstek_venus_2_battery_power']['last_reported'] = (NOW+timedelta(seconds=1)).isoformat()
        report = assess(states, now=NOW, max_age_s=ages, attested=evidence)
        self.assertEqual(report.sources['sensor.senec_solar_generated_power'].reason, 'invalid_value')
        self.assertEqual(report.sources['sensor.marstek_venus_1_battery_soc'].reason, 'invalid_value')
        self.assertEqual(report.sources['sensor.marstek_venus_2_battery_power'].reason, 'invalid_or_stale_last_reported')

    def test_excluded_house_sensor_cannot_change_result(self):
        ages, evidence = test_config()
        states = snapshot()
        baseline = assess(states, now=NOW, max_age_s=ages, attested=evidence)
        states['sensor.senec_house_power'] = {'state': '999999', 'attributes': {}}
        self.assertEqual(assess(states, now=NOW, max_age_s=ages, attested=evidence), baseline)
        self.assertNotIn('sensor.senec_house_power', REQUIRED)
    def test_failed_quality_cannot_be_overridden_by_synthetic_verified_input(self):
        report = assess(snapshot(age=100), now=NOW, max_age_s={}, attested={})
        claimed = Inputs('idle', 'import', False, True, {1: 80, 2: 80})
        self.assertEqual(decide_checked('gate_venus_1', claimed, report),
                         {1: 'idle_alarm', 2: 'unmanaged'})
    def test_missing_policy_objects_fail_closed_without_exception(self):
        report = assess(snapshot(), now=NOW, max_age_s=None, attested=None)
        self.assertFalse(report.ready)
        self.assertTrue(all(x.reason == 'unvalidated_policy' for x in report.sources.values()))


if __name__ == '__main__':
    unittest.main()
