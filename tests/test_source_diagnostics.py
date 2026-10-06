"""Read-only HA source inventory: no calibration, classification or control."""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from offline_package import prepare

prepare()
from custom_components.senec_marstek_gate.quality import REQUIRED
from custom_components.senec_marstek_gate.source_diagnostics import inspect_sources


class FakeStates:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def get(self, entity_id):
        self.calls.append(entity_id)
        return self.data.get(entity_id)


def state(entity, value='123', unit='W', stamp=None):
    stamp = stamp or datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
    return SimpleNamespace(entity_id=entity, state=value, last_reported=stamp,
                           attributes={'unit_of_measurement': unit,
                                       'device_class': 'battery' if unit == '%' else 'power',
                                       'state_class': 'measurement'})


class SourceDiagnosticsTest(unittest.TestCase):
    def test_reads_only_required_sources_and_never_exposes_measurements(self):
        data = {entity: state(entity, value='50' if unit == '%' else '123', unit=unit)
                for entity, unit in REQUIRED.items()}
        data['sensor.senec_house_power'] = state('sensor.senec_house_power')
        states = FakeStates(data)
        result = inspect_sources(states)
        self.assertEqual(states.calls, list(REQUIRED))
        self.assertEqual(set(result), set(REQUIRED))
        self.assertEqual(set(result.values()), {'observed_not_attested'})
        self.assertNotIn('123', repr(result))

    def test_missing_unknown_and_bad_metadata_are_not_observed(self):
        first, second, third = list(REQUIRED)[:3]
        data = {second: state(second, value='unknown'),
                third: state(third, unit='kW')}
        result = inspect_sources(FakeStates(data))
        self.assertEqual(result[first], 'missing')
        self.assertEqual(result[second], 'invalid_value')
        self.assertEqual(result[third], 'metadata_mismatch')

    def test_timestamp_is_not_mistaken_for_verified_heartbeat(self):
        entity = next(iter(REQUIRED))
        old = state(entity, stamp=datetime(2020, 1, 1, tzinfo=timezone.utc))
        future = state(entity, stamp=datetime(2099, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(inspect_sources(FakeStates({entity: old}))[entity], 'observed_not_attested')
        self.assertEqual(inspect_sources(FakeStates({entity: future}))[entity], 'observed_not_attested')
        no_timestamp = state(entity)
        no_timestamp.last_reported = None
        self.assertEqual(inspect_sources(FakeStates({entity: no_timestamp}))[entity], 'missing_report_timestamp')

    def test_boolean_state_is_not_a_numeric_power_reading(self):
        entity = next(iter(REQUIRED))
        self.assertEqual(inspect_sources(FakeStates({entity: state(entity, value=True)}))[entity],
                         'invalid_value')

    def test_failed_read_becomes_unknown_not_zero(self):
        class Broken:
            def get(self, entity_id):
                raise RuntimeError('registry not ready')
        self.assertEqual(set(inspect_sources(Broken()).values()), {'read_error'})


if __name__ == '__main__':
    unittest.main()
