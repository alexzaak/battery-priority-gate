"""Offline authority: approval/revocation and source fallback; no HA I/O."""
import unittest
from offline_package import prepare
prepare()
from custom_components.senec_marstek_gate.authority import Authority, Pending, advance


class AuthorityTest(unittest.TestCase):
    def test_default_and_restart_inhibit_all_writes(self):
        state = Authority()
        self.assertEqual((state.ownership, state.source, state.inhibited), ('manual', 'enfluri', True))
        state = advance(Authority('gate_venus_1', 'tibber', False), 'restart')
        self.assertEqual((state.ownership, state.source, state.inhibited), ('gate_venus_1', 'tibber', True))
        self.assertIsNone(state.pending)

    def test_named_request_only_confirmed_after_fresh_revalidation(self):
        state = advance(Authority(inhibited=False), 'request_device', device=1, request_id='one')
        self.assertEqual(state.pending, Pending('device', 1, 'one'))
        wrong = advance(state, 'approve', request_id='other', healthy=True, handover=True)
        self.assertEqual(wrong.ownership, 'manual')
        stale = advance(state, 'approve', request_id='one', healthy=False, handover=True)
        self.assertEqual(stale.ownership, 'manual')
        accepted = advance(state, 'approve', request_id='one', healthy=True, handover=True)
        self.assertEqual(accepted.ownership, 'gate_venus_1')
        self.assertIsNone(accepted.pending)
        self.assertEqual(advance(accepted, 'approve', request_id='one', healthy=True,
                                 handover=True).ownership, 'gate_venus_1')
        second = advance(accepted, 'request_device', device=2, request_id='two')
        both = advance(second, 'approve', request_id='two', healthy=True, handover=True)
        self.assertEqual(both.ownership, 'gate_beide')

    def test_manual_known_or_unknown_revokes_without_writes(self):
        both = Authority('gate_beide', 'enfluri', False)
        self.assertEqual(advance(both, 'manual', device=2).ownership, 'gate_venus_1')
        self.assertEqual(advance(both, 'manual').ownership, 'manual')
        self.assertEqual(advance(both, 'manual_helper_off', device=1).ownership, 'gate_beide')
        self.assertEqual(advance(both, 'manual_helper_on', device=1).ownership, 'gate_venus_2')

    def test_maintenance_revokes_and_off_does_not_grant(self):
        started = advance(Authority('gate_beide', 'tibber', False), 'maintenance_on')
        self.assertEqual((started.ownership, started.inhibited), ('manual', True))
        stopped = advance(started, 'maintenance_off')
        self.assertEqual((stopped.ownership, stopped.inhibited), ('manual', True))
        self.assertEqual(stopped.source, 'tibber')

    def test_maintenance_cannot_be_bypassed_by_revalidate(self):
        state = advance(Authority('gate_beide', 'enfluri', False), 'maintenance_on')
        state = advance(state, 'revalidate', healthy=True, primary_ready=True)
        self.assertTrue(state.inhibited)
        self.assertEqual(state.ownership, 'manual')
        self.assertIsNone(advance(state, 'request_device', device=1, request_id='bad').pending)
        off = advance(state, 'maintenance_off')
        self.assertTrue(off.inhibited)
        self.assertFalse(off.maintenance)

    def test_fallback_requires_matching_approval_and_current_primary_failure(self):
        state = advance(Authority(inhibited=False), 'request_fallback', request_id='grid-fail',
                        primary_failed=True)
        self.assertEqual(state.pending, Pending('source', 'tibber', 'grid-fail'))
        rejected = advance(state, 'approve', request_id='grid-fail', healthy=True,
                           primary_failed=False, alternate_ready=True)
        self.assertEqual(rejected.source, 'enfluri')
        accepted = advance(state, 'approve', request_id='grid-fail', healthy=True,
                           primary_failed=True, alternate_ready=True)
        self.assertEqual((accepted.source, accepted.inhibited), ('tibber', True))
        self.assertIsNone(accepted.pending)
        self.assertEqual(advance(accepted, 'revalidate', healthy=True,
                                 alternate_ready=True).inhibited, False)

    def test_fallback_return_requires_separate_approval(self):
        state = advance(Authority(source='tibber', inhibited=False), 'request_return',
                        request_id='grid-return', primary_ready=True)
        self.assertEqual(state.pending, Pending('source', 'enfluri', 'grid-return'))
        self.assertEqual(advance(state, 'approve', request_id='grid-return', healthy=True,
                                 primary_ready=False).source, 'tibber')
        returned = advance(state, 'approve', request_id='grid-return', healthy=True,
                           primary_ready=True)
        self.assertEqual((returned.source, returned.inhibited), ('enfluri', True))

    def test_source_change_never_implies_device_approval(self):
        state = advance(Authority(), 'request_fallback', request_id='source', primary_failed=True)
        state = advance(state, 'approve', request_id='source', healthy=True,
                        primary_failed=True, alternate_ready=True)
        self.assertEqual(state.ownership, 'manual')
        self.assertTrue(state.inhibited)


if __name__ == '__main__':
    unittest.main()
