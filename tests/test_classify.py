"""Offline direction classifier tests; thresholds synthetic, dated fixture has no heartbeat."""
import unittest
import json
from pathlib import Path
from datetime import datetime, timezone
from offline_package import prepare
prepare()
from custom_components.senec_marstek_gate.quality import QualityReport, SourceQuality, REQUIRED, assess
from custom_components.senec_marstek_gate.classify import classify, Thresholds, counterflow, Directions


def report(values, *, faulty=()):
    return QualityReport({eid: SourceQuality(eid not in faulty, 'invalid' if eid in faulty else 'ok',
                                            values.get(eid)) for eid in REQUIRED})


def values(**overrides):
    base = {eid: (70 if eid.endswith('_battery_soc') else 0) for eid in REQUIRED}
    base.update(overrides)
    return base


class ClassifierTest(unittest.TestCase):
    def test_senec_positive_is_charge_and_venus_negative_is_discharge(self):
        r = report(values(**{'sensor.senec_battery_state_power': 300,
                             'sensor.marstek_venus_1_battery_power': -400,
                             'sensor.marstek_venus_2_battery_power': 200}))
        out = classify(r, Thresholds(senec_w=80, venus_w=30, grid_w=100))
        self.assertEqual(out.senec, 'charge')
        self.assertEqual(out.venus, {1: 'discharge', 2: 'charge'})

    def test_invalid_source_never_becomes_direction_or_control_ready(self):
        eid='sensor.marstek_venus_2_battery_power'
        r=report(values(**{eid: -500}), faulty=(eid,))
        out=classify(r, Thresholds(senec_w=80, venus_w=30, grid_w=100))
        self.assertFalse(out.ready)
        self.assertEqual(out.venus[2], 'unknown')

    def test_missing_or_nonpositive_thresholds_fail_closed(self):
        r=report(values(**{'sensor.marstek_venus_2_battery_power': -5}))
        for limits in (None, Thresholds(80, 0, 100), Thresholds(80, float('nan'), 100)):
            with self.subTest(limits=limits):
                out=classify(r, limits)
                self.assertFalse(out.ready)
                self.assertEqual(out.venus[2], 'unknown')

    def test_near_zero_and_grid_signs_with_exploratory_deadzones(self):
        r=report(values(**{'sensor.senec_battery_state_power': 0,
                           'sensor.marstek_venus_1_battery_power': -7,
                           'sensor.marstek_venus_2_battery_power': -30,
                           'sensor.senec_enfluri_net_power_total': 100}))
        out=classify(r, Thresholds(senec_w=80, venus_w=30, grid_w=100))
        self.assertEqual(out.senec, 'idle')
        self.assertEqual(out.venus, {1: 'idle', 2: 'idle'})
        self.assertEqual(out.grid, 'neutral')

    def test_counterflow_detects_senec_charge_with_venus_discharge_even_at_neutral_grid(self):
        r=report(values(**{'sensor.senec_battery_state_power': 262.5,
                           'sensor.marstek_venus_1_battery_power': -428,
                           'sensor.marstek_venus_2_battery_power': -5,
                           'sensor.senec_enfluri_net_power_total': -15}))
        result=classify(r, Thresholds(senec_w=80, venus_w=30, grid_w=100))
        self.assertEqual(counterflow(result), frozenset({1}))

    def test_counterflow_detects_reverse_pattern(self):
        r=report(values(**{'sensor.senec_battery_state_power': -500,
                           'sensor.marstek_venus_1_battery_power': 150,
                           'sensor.marstek_venus_2_battery_power': -6}))
        self.assertEqual(counterflow(classify(r, Thresholds(80, 30, 100))), frozenset({1}))

    def test_bad_quality_is_unknown_not_no_counterflow(self):
        eid='sensor.marstek_venus_1_battery_power'
        r=report(values(**{'sensor.senec_battery_state_power': 250, eid: -500}), faulty=(eid,))
        self.assertIsNone(counterflow(classify(r, Thresholds(80, 30, 100))))

    def test_pv_generation_and_export_do_not_prove_pv_surplus(self):
        r=report(values(**{'sensor.senec_solar_generated_power': 1200,
                           'sensor.senec_enfluri_net_power_total': -400}))
        out=classify(r, Thresholds(80, 30, 100))
        self.assertEqual(out.grid, 'export')
        self.assertFalse(out.pv_surplus_proven)

    def test_invalid_numeric_payload_fails_closed_even_if_quality_report_claims_ready(self):
        r=report(values(**{'sensor.marstek_venus_2_battery_power': None}))
        out=classify(r, Thresholds(80, 30, 100))
        self.assertFalse(out.ready)
        self.assertIsNone(counterflow(out))

    def test_dated_history_without_last_reported_cannot_be_classified_as_fresh(self):
        fixture=json.loads((Path(__file__).resolve().parents[1] /
                            'fixtures/incident_2026-10-05_062900.json').read_text())
        self.assertFalse(fixture['has_last_reported'])
        now=datetime(2026,10,5,4,29,tzinfo=timezone.utc)
        states={eid:{'entity_id':eid,
                     'state': str(fixture['readings'][eid]['value_w'])
                     if eid in fixture['readings'] and 'value_w' in fixture['readings'][eid]
                     else '50' if eid.endswith('_battery_soc') else '0',
                     'attributes':{'unit_of_measurement':unit,
                                   'device_class':'battery' if unit=='%' else 'power',
                                   'state_class':'measurement'}} for eid,unit in REQUIRED.items()}
        policy={eid:10 for eid in REQUIRED}
        attestation={eid:{'topology':True,'sign':True,'heartbeat':True} for eid in REQUIRED}
        quality=assess(states,now=now,max_age_s=policy,attested=attestation)
        self.assertFalse(quality.ready)
        self.assertIsNone(counterflow(classify(quality,Thresholds(80,30,100))))

    def test_unknown_direction_is_unknown_not_empty_conflict(self):
        forged=Directions('unknown',{1:'idle',2:'discharge'},'neutral',True)
        self.assertIsNone(counterflow(forged))

if __name__ == '__main__':
    unittest.main()
