"""Offline contract tests for an inert, installable Home Assistant integration."""
import asyncio
import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'custom_components' / 'senec_marstek_gate'


class FakeEntries:
    def __init__(self):
        self.forwarded = []
        self.unloaded = []

    async def async_forward_entry_setups(self, entry, platforms):
        self.forwarded.append((entry, platforms))

    async def async_unload_platforms(self, entry, platforms):
        self.unloaded.append((entry, platforms))
        return True


class FakeHass:
    def __init__(self):
        self.config_entries = FakeEntries()
        self.services = types.SimpleNamespace(async_call=lambda *a, **kw: (_ for _ in ()).throw(AssertionError('actuator call')))


class IntegrationContract(unittest.TestCase):
    def test_setup_forwards_only_read_only_sensor(self):
        fake_core = types.ModuleType('homeassistant.core')
        fake_core.HomeAssistant = FakeHass
        fake_config = types.ModuleType('homeassistant.config_entries')
        fake_config.ConfigEntry = type('ConfigEntry', (), {})
        with patch.dict(sys.modules, {'homeassistant': types.ModuleType('homeassistant'),
                                      'homeassistant.core': fake_core,
                                      'homeassistant.config_entries': fake_config}):
            module = importlib.import_module('custom_components.senec_marstek_gate')
            hass, entry = FakeHass(), object()
            self.assertTrue(asyncio.run(module.async_setup_entry(hass, entry)))
            self.assertEqual(hass.config_entries.forwarded, [(entry, ['sensor'])])
            self.assertTrue(asyncio.run(module.async_unload_entry(hass, entry)))
            self.assertEqual(hass.config_entries.unloaded, [(entry, ['sensor'])])
        sys.modules.pop('custom_components.senec_marstek_gate', None)

    def test_packaged_classifier_resolves_its_local_quality_module(self):
        fake_core = types.ModuleType('homeassistant.core')
        fake_core.HomeAssistant = FakeHass
        fake_config = types.ModuleType('homeassistant.config_entries')
        fake_config.ConfigEntry = type('ConfigEntry', (), {})
        with patch.dict(sys.modules, {'homeassistant': types.ModuleType('homeassistant'),
                                      'homeassistant.core': fake_core,
                                      'homeassistant.config_entries': fake_config}):
            module = importlib.import_module('custom_components.senec_marstek_gate.classify')
            self.assertTrue(module.Thresholds)
            self.assertEqual(module.QualityReport.__module__, 'custom_components.senec_marstek_gate.quality')
            self.assertFalse(module.Directions('idle', {1: 'idle', 2: 'idle'}, 'neutral', True).pv_surplus_proven)
        for name in ('custom_components.senec_marstek_gate.classify',
                     'custom_components.senec_marstek_gate.quality',
                     'custom_components.senec_marstek_gate'):
            sys.modules.pop(name, None)

    def test_status_sensor_reads_sources_without_changing_inactive_state(self):
        fake_core = types.ModuleType('homeassistant.core')
        fake_core.HomeAssistant = FakeHass
        fake_config = types.ModuleType('homeassistant.config_entries')
        fake_config.ConfigEntry = type('ConfigEntry', (), {})
        fake_sensor = types.ModuleType('homeassistant.components.sensor')
        fake_sensor.SensorEntity = type('SensorEntity', (), {})
        fake_platform = types.ModuleType('homeassistant.helpers.entity_platform')
        fake_platform.AddEntitiesCallback = type('AddEntitiesCallback', (), {})
        from offline_package import prepare
        prepare()
        with patch.dict(sys.modules, {
            'homeassistant': types.ModuleType('homeassistant'),
            'homeassistant.core': fake_core,
            'homeassistant.config_entries': fake_config,
            'homeassistant.components': types.ModuleType('homeassistant.components'),
            'homeassistant.components.sensor': fake_sensor,
            'homeassistant.helpers': types.ModuleType('homeassistant.helpers'),
            'homeassistant.helpers.entity_platform': fake_platform,
        }):
            module = importlib.import_module('custom_components.senec_marstek_gate.sensor')
            added = []
            hass = FakeHass()
            calls = []
            hass.states = types.SimpleNamespace(get=lambda entity: calls.append(entity) or None)
            entry = types.SimpleNamespace(entry_id='example')
            asyncio.run(module.async_setup_entry(hass, entry, added.extend))
            self.assertEqual(len(added), 1)
            entity = added[0]
            entity.hass = hass
            asyncio.run(entity.async_update())
            self.assertEqual(entity._attr_native_value, 'inaktiv')
            self.assertEqual(len(calls), 7)
            self.assertEqual(set(entity._attr_extra_state_attributes['source_checks'].values()), {'missing'})
            self.assertFalse(entity._attr_extra_state_attributes['production_ready'])
            self.assertEqual(entity._attr_extra_state_attributes['runtime_version'], '0.4.0')
            from datetime import datetime, timezone
            valid = types.SimpleNamespace(
                entity_id='sensor.senec_enfluri_net_power_total', state='140',
                attributes={'unit_of_measurement': 'W', 'device_class': 'power',
                            'state_class': 'measurement'},
                last_reported=datetime(2026, 10, 6, tzinfo=timezone.utc))
            hass.states.get = lambda entity_id: valid if entity_id == valid.entity_id else None
            asyncio.run(entity.async_update())
            self.assertEqual(entity._attr_extra_state_attributes['source_checks'][valid.entity_id],
                             'observed_not_attested')
            self.assertEqual(entity._attr_native_value, 'inaktiv')
            self.assertFalse(entity._attr_extra_state_attributes['production_ready'])
            self.assertNotIn('140', repr(entity._attr_extra_state_attributes))
        sys.modules.pop('custom_components.senec_marstek_gate.sensor', None)

    def test_manifest_is_discoverable_but_control_cannot_be_enabled(self):
        manifest = json.loads((COMPONENT / 'manifest.json').read_text())
        version_source = (COMPONENT / 'version.py').read_text()
        self.assertIn("VERSION = '0.4.0'", version_source)
        self.assertEqual(manifest['domain'], 'senec_marstek_gate')
        self.assertTrue(manifest['config_flow'])
        self.assertEqual(manifest['version'], '0.4.0')
        source = '\n'.join((COMPONENT / p).read_text() for p in ('__init__.py', 'sensor.py', 'config_flow.py'))
        for forbidden in ('async_call(', 'call_service(', 'number.set_value',
                          'switch.turn_on', 'switch.turn_off', 'verified=True', 'from .writer import',
                          'import writer', 'apply_request('):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
