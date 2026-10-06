"""Repository contract required for HACS custom integration discovery."""
import json
import struct
import unittest
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ROOT / 'custom_components'
INTEGRATION = COMPONENTS / 'senec_marstek_gate'
REPO = 'https://github.com/alexzaak/battery-priority-gate'


class HacsRepositoryTest(unittest.TestCase):
    def test_license_is_present(self):
        license_text = (ROOT / 'LICENSE').read_text()
        self.assertIn('MIT License', license_text)
        self.assertIn('Copyright (c) 2026 alexzaak', license_text)

    def test_single_integration_with_required_manifest_links(self):
        self.assertEqual([p.name for p in COMPONENTS.iterdir() if p.is_dir() and not p.name.startswith('__')],
                         ['senec_marstek_gate'])
        manifest = json.loads((INTEGRATION / 'manifest.json').read_text())
        self.assertEqual(manifest['domain'], INTEGRATION.name)
        self.assertEqual(manifest['documentation'], REPO + '#readme')
        self.assertEqual(manifest['issue_tracker'], REPO + '/issues')
        self.assertIsInstance(manifest['codeowners'], list)
        self.assertTrue(manifest['name'])
        self.assertTrue(manifest['version'])
        for key in ('documentation', 'issue_tracker'):
            self.assertEqual(urlparse(manifest[key]).scheme, 'https')

    def test_hacs_manifest_and_local_icon(self):
        hacs = json.loads((ROOT / 'hacs.json').read_text())
        self.assertTrue(hacs['name'])
        icon = (INTEGRATION / 'brand' / 'icon.png').read_bytes()
        self.assertEqual(icon[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(struct.unpack('>II', icon[16:24]), (256, 256))


if __name__ == '__main__':
    unittest.main()
