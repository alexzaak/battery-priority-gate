"""Offline controller contract: proposed requests only, never HA service calls."""
import unittest
from datetime import datetime, timedelta, timezone

from offline_package import prepare
prepare()
from custom_components.senec_marstek_gate.quality import QualityReport, SourceQuality, REQUIRED
from custom_components.senec_marstek_gate.controller import (CycleState, Evidence, Limits, evaluate)

NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)


def quality(grid=-400, pv=1200, senec=0, venus1=0, venus2=0, soc1=50, soc2=50):
    values = [grid, senec, pv, venus1, venus2, soc1, soc2]
    return QualityReport({eid: SourceQuality(True, 'ok', val) for eid, val in zip(REQUIRED, values)})


LIMITS = Limits(grid_deadband_w=100, senec_deadband_w=80, venus_deadband_w=30,
                max_charge_w={1: 300, 2: 300}, max_discharge_w={1: 300, 2: 300},
                stable_s=180, soc_resume_pct=14)


def evidence(**overrides):
    data = dict(source_attested=True, pv_surplus_attested=True, exclusive={1: True, 2: True},
                maintenance=False, handover_confirmed={1: True, 2: True})
    data.update(overrides)
    return Evidence(**data)


class ControllerTest(unittest.TestCase):
    def test_manual_counterflow_warns_without_stop(self):
        result = evaluate(CycleState('manual'), quality(senec=-400, venus1=250),
                          evidence(), LIMITS, NOW)
        self.assertEqual(result.requests, {1: ('warn', None), 2: ('unmanaged', None)})

    def test_unowned_counterflow_warns_only_that_device(self):
        result = evaluate(CycleState('gate_venus_2'), quality(senec=400, venus1=-250),
                          evidence(), LIMITS, NOW)
        self.assertEqual(result.requests[1], ('warn', None))
        self.assertNotEqual(result.requests[2][0], 'warn')

    def test_unverified_quality_never_proposes_charge(self):
        bad = QualityReport({eid: SourceQuality(False, 'stale') for eid in REQUIRED})
        result = evaluate(CycleState('gate_beide', NOW - timedelta(minutes=4)), bad,
                          evidence(), LIMITS, NOW)
        self.assertEqual(result.requests, {1: ('zero_intent', 0), 2: ('zero_intent', 0)})

    def test_unknown_ownership_never_sends_even_stop_intent(self):
        result = evaluate(CycleState('unknown', NOW - timedelta(minutes=4)), quality(),
                          evidence(), LIMITS, NOW)
        self.assertEqual(result.requests, {1: ('warn', None), 2: ('warn', None)})

    def test_manual_override_never_gets_device_request(self):
        result = evaluate(CycleState('gate_beide', NOW - timedelta(minutes=4),
                                     ('gate_beide', 'charge'), NOW - timedelta(seconds=2)), quality(),
                          evidence(exclusive={1: False, 2: True}), LIMITS, NOW)
        self.assertEqual(result.requests[1], ('warn', None))
        self.assertEqual(result.requests[2][0], 'charge_intent')

    def test_gate_release_requires_stability_then_caps_two_devices_together(self):
        initial = evaluate(CycleState('gate_beide'), quality(), evidence(), LIMITS, NOW)
        self.assertEqual(initial.requests, {1: ('hold', None), 2: ('hold', None)})
        state = initial.state
        for seconds in range(10, 190, 10):
            later = evaluate(state, quality(), evidence(), LIMITS, NOW + timedelta(seconds=seconds))
            state = later.state
        self.assertEqual(later.requests, {1: ('charge_intent', 300), 2: ('hold', None)})
        self.assertEqual(later.state.stable_since, NOW)

    def test_owning_senec_discharge_blocks_charge_and_resets_timer(self):
        result = evaluate(CycleState('gate_beide', NOW - timedelta(minutes=4)),
                          quality(senec=-300), evidence(), LIMITS, NOW)
        self.assertEqual(result.requests, {1: ('zero_intent', 0), 2: ('zero_intent', 0)})
        self.assertIsNone(result.state.stable_since)

    def test_import_discharge_needs_soc_above_reserve_and_senec_idle(self):
        result = evaluate(CycleState('gate_beide', NOW - timedelta(minutes=4),
                                     ('gate_beide', 'discharge'), NOW - timedelta(seconds=2)),
                          quality(grid=650, pv=0, soc1=12, soc2=60), evidence(), LIMITS, NOW)
        self.assertEqual(result.requests[1], ('hold', None))
        self.assertEqual(result.requests[2], ('discharge_intent', 300))

    def test_no_pv_attestation_no_charge_despite_export(self):
        result = evaluate(CycleState('gate_beide', NOW - timedelta(minutes=4)), quality(),
                          evidence(pv_surplus_attested=False), LIMITS, NOW)
        self.assertEqual(result.requests, {1: ('zero_intent', 0), 2: ('zero_intent', 0)})

    def test_restart_restarts_stability_window(self):
        before = evaluate(CycleState('gate_beide'), quality(), evidence(), LIMITS, NOW)
        after = evaluate(CycleState('gate_beide'), quality(), evidence(), LIMITS,
                         NOW + timedelta(minutes=4))
        self.assertEqual(before.requests, {1: ('hold', None), 2: ('hold', None)})
        self.assertEqual(after.requests, {1: ('hold', None), 2: ('hold', None)})

    def test_mode_change_resets_stability_window(self):
        old = CycleState('gate_beide', NOW - timedelta(minutes=4), ('gate_beide', 'charge'))
        result = evaluate(old, quality(grid=650, pv=0), evidence(), LIMITS, NOW)
        self.assertEqual(result.requests, {1: ('hold', None), 2: ('hold', None)})
        self.assertEqual(result.state.stable_since, NOW)

    def test_missing_evaluation_tick_resets_stability_window(self):
        old = CycleState('gate_beide', NOW - timedelta(minutes=5), ('gate_beide', 'charge'),
                         NOW - timedelta(minutes=5))
        result = evaluate(old, quality(), evidence(), LIMITS, NOW)
        self.assertEqual(result.requests, {1: ('hold', None), 2: ('hold', None)})
        self.assertEqual(result.state.stable_since, NOW)

    def test_manually_owned_device_does_not_consume_other_devices_budget(self):
        result = evaluate(CycleState('gate_venus_2', NOW - timedelta(minutes=4),
                                     ('gate_venus_2', 'charge'), NOW - timedelta(seconds=2)),
                          quality(grid=-400), evidence(), LIMITS, NOW)
        self.assertEqual(result.requests[1], ('unmanaged', None))
        self.assertEqual(result.requests[2], ('charge_intent', 300))

    def test_at_soc_resume_boundary_discharge_stays_blocked(self):
        result = evaluate(CycleState('gate_venus_1', NOW - timedelta(minutes=4),
                                     ('gate_venus_1', 'discharge'), NOW - timedelta(seconds=2)),
                          quality(grid=650, pv=0, soc1=14, venus1=-200), evidence(), LIMITS, NOW)
        self.assertEqual(result.requests[1], ('zero_intent', 0))

    def test_empty_quality_and_unproven_source_never_authorize(self):
        result = evaluate(CycleState('gate_beide'), QualityReport({}), evidence(), LIMITS, NOW)
        self.assertEqual(result.requests, {1: ('zero_intent', 0), 2: ('zero_intent', 0)})

    def test_maintenance_and_missing_handover_do_not_write(self):
        result = evaluate(CycleState('gate_beide'), quality(),
                          evidence(maintenance=True, handover_confirmed={1: False, 2: False}), LIMITS, NOW)
        self.assertEqual(result.requests, {1: ('warn', None), 2: ('warn', None)})

    def test_invalid_limits_fail_closed(self):
        limits = Limits(0, 80, 30, {1: 300, 2: 300}, {1: 300, 2: 300}, 180, 14)
        result = evaluate(CycleState('gate_beide', NOW - timedelta(minutes=4)), quality(),
                          evidence(), limits, NOW)
        self.assertEqual(result.requests, {1: ('zero_intent', 0), 2: ('zero_intent', 0)})


if __name__ == '__main__':
    unittest.main()
