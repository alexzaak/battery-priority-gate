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

    def test_manifest_is_discoverable_but_control_cannot_be_enabled(self):
        manifest = json.loads((COMPONENT / 'manifest.json').read_text())
        self.assertEqual(manifest['domain'], 'senec_marstek_gate')
        self.assertTrue(manifest['config_flow'])
        self.assertTrue(manifest['version'])
        source = '\n'.join(p.read_text() for p in COMPONENT.glob('*.py'))
        for forbidden in ('async_call(', 'call_service(', 'number.set_value',
                          'switch.turn_on', 'switch.turn_off', 'verified=True'):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
