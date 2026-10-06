"""Offline contract tests. No HA connection or actuator calls."""
import unittest
from offline_package import prepare
prepare()
from custom_components.senec_marstek_gate.gate import transition, decide, Inputs


class OwnershipTest(unittest.TestCase):
    def test_manual_intervention_revokes_only_named_device(self):
        self.assertEqual(transition('gate_beide', 'manual', device=2), ('gate_venus_1', 'notify'))
        self.assertEqual(transition('gate_beide', 'manual', device=None), ('manual', 'notify'))
    def test_maintenance_and_restart_never_grant_write_authority(self):
        self.assertEqual(transition('gate_beide', 'maintenance_on')[0], 'manual')
        self.assertEqual(transition('manual', 'maintenance_off')[0], 'manual')
        self.assertEqual(transition('gate_venus_1', 'maintenance_off')[1], 'block_vendor_handover_and_alarm')
        self.assertEqual(transition('gate_venus_1', 'restart')[1], 'inhibit_writes_until_revalidated')

    def test_grant_requires_explicit_approval_health_and_no_maintenance(self):
        for kw in ({}, {'approval': True}, {'healthy': True}, {'approval': True, 'healthy': True, 'maintenance': True}):
            self.assertEqual(transition('manual', 'grant', device=1, **kw)[0], 'manual')
        self.assertEqual(transition('manual', 'grant', device=1, approval=True, healthy=True)[0], 'gate_venus_1')

    def test_unknown_ownership_fails_closed(self):
        self.assertEqual(transition('unexpected', 'grant', device=1, approval=True, healthy=True)[0], 'manual')


class DecisionTest(unittest.TestCase):
    def test_no_permission_without_verified_grid_and_soc(self):
        x = Inputs(senec='idle', grid='import', pv_surplus=False, verified=False, soc={1: 80, 2: 80})
        self.assertEqual(decide('gate_beide', x), {1: 'idle_alarm', 2: 'idle_alarm'})

    def test_discharge_requires_idle_import_and_soc_above_reserve(self):
        x = Inputs(senec='idle', grid='import', pv_surplus=False, verified=True, soc={1: 12, 2: 80})
        self.assertEqual(decide('gate_beide', x), {1: 'idle', 2: 'discharge_candidate'})

    def test_pv_charge_forbidden_on_senec_discharge(self):
        x = Inputs(senec='discharge', grid='export', pv_surplus=True, verified=True, soc={1: 80, 2: 80})
        self.assertEqual(decide('gate_beide', x), {1: 'idle', 2: 'idle'})

    def test_pv_charge_allowed_during_senec_charge_without_import(self):
        x = Inputs(senec='charge', grid='export', pv_surplus=True, verified=True, soc={1: 80, 2: 80})
        self.assertEqual(decide('gate_venus_1', x), {1: 'charge_candidate', 2: 'unmanaged'})
    def test_missing_soc_and_contradictory_surplus_fail_closed_per_owned_device(self):
        x = Inputs('idle', 'import', True, True, {1: None, 2: 70})
        self.assertEqual(decide('gate_venus_1', x), {1: 'idle_alarm', 2: 'unmanaged'})
        x = Inputs('idle', 'export', True, True, {1: None, 2: 70})
        self.assertEqual(decide('gate_beide', x), {1: 'idle_alarm', 2: 'charge_candidate'})

    def test_unknown_sensor_direction_fails_closed(self):
        x = Inputs('unknown', 'export', True, True, {1: 50, 2: 50})
        self.assertEqual(decide('gate_beide', x), {1: 'idle_alarm', 2: 'idle_alarm'})

    def test_no_grid_import_no_discharge(self):
        x = Inputs('idle', 'neutral', False, True, {1: 50, 2: 50})
        self.assertEqual(decide('gate_beide', x), {1: 'idle', 2: 'idle'})


if __name__ == '__main__':
    unittest.main()
