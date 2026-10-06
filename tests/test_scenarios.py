"""Offline scenario matrix: classifications are synthetic unless explicitly sourced.

A small dated HA-history excerpt corroborates the 05.10.2026 direction pattern;
missing last_reported prevents a full telemetry-freshness or causal replay.
"""
import unittest
import json
from pathlib import Path
from offline_package import prepare
prepare()
from custom_components.senec_marstek_gate.gate import Inputs, decide, audit


class ScenarioMatrix(unittest.TestCase):
    def test_charge_and_discharge_policy_matrix(self):
        # No physical PV/export watts are asserted here; classifications are assumptions.
        cases = [
            ('senec_discharge_export_blocks_charge', 'discharge', 'export', True, 80, 'idle'),
            ('senec_charge_export_allows_pv_charge_candidate', 'charge', 'export', True, 80, 'charge_candidate'),
            ('senec_idle_export_allows_pv_charge_candidate', 'idle', 'export', True, 80, 'charge_candidate'),
            ('export_without_pv_proof_blocks_charge', 'idle', 'export', False, 80, 'idle'),
            ('heat_pump_load_no_import_blocks_discharge', 'idle', 'neutral', False, 80, 'idle'),
            ('actual_import_idle_above_reserve_candidate', 'idle', 'import', False, 80, 'discharge_candidate'),
            ('reserve_exactly_12_blocks_discharge', 'idle', 'import', False, 12, 'idle'),
            ('senec_charge_blocks_discharge_on_import', 'charge', 'import', False, 80, 'idle'),
        ]
        for name, senec, grid, pv, soc, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(decide('gate_venus_2', Inputs(senec, grid, pv, True, {2: soc})),
                                 {1: 'unmanaged', 2: expected})

    def test_historical_pattern_detects_venus2_charging_against_senec_discharge(self):
        # Only the directional overlap is grounded in the dated history.
        decision = decide('gate_beide', Inputs('discharge', 'export', False, True, {1: 80, 2: 80}))
        self.assertEqual(audit('gate_beide', 'discharge', decision,
                               {1: 'idle', 2: 'charge'}, data_ok=True),
                         {1: 'none', 2: 'stop_intent_alarm'})

    def test_same_counterflow_in_manual_device_only_warns(self):
        decision = decide('gate_venus_1', Inputs('discharge', 'export', False, True, {1: 80}))
        self.assertEqual(audit('gate_venus_1', 'discharge', decision,
                               {1: 'idle', 2: 'charge'}, data_ok=True),
                         {1: 'none', 2: 'warn_manual'})
    def test_quality_failure_even_with_idle_battery_power_requests_alarm_only_for_owned(self):
        expected = {1: 'idle_alarm', 2: 'unmanaged'}
        self.assertEqual(audit('gate_venus_1', 'idle', expected,
                               {1: 'idle', 2: 'idle'}, data_ok=False),
                         {1: 'stop_intent_alarm', 2: 'warn_manual'})

    def test_idle_alarm_intent_is_not_silently_dismissed_by_idle_battery_power(self):
        self.assertEqual(audit('gate_venus_1', 'idle',
                               {1: 'idle_alarm', 2: 'unmanaged'},
                               {1: 'idle', 2: 'idle'}, data_ok=True)[1],
                         'stop_intent_alarm')
    def test_dated_history_excerpt_matches_directional_regression_only(self):
        path = Path(__file__).resolve().parents[1] / 'fixtures/incident_2026-10-05_062900.json'
        fixture = json.loads(path.read_text())
        self.assertEqual(fixture['source'], 'HA REST /api/history/period')
        self.assertFalse(fixture['has_last_reported'])
        senec = fixture['readings']['sensor.senec_battery_state_power']['value_w']
        venus = fixture['readings']['sensor.marstek_venus_2_battery_power']['value_w']
        self.assertLess(senec, 0)
        self.assertGreater(venus, 0)
        expected = decide('gate_venus_2', Inputs('discharge', 'export', False, True, {2: 17}))
        # Historical raw directions corroborate the qualitative pattern, not a full replay.
        self.assertEqual(audit('gate_venus_2', 'discharge', expected,
                               {1: 'idle', 2: 'charge'}, data_ok=True)[2], 'stop_intent_alarm')


if __name__ == '__main__':
    unittest.main()
