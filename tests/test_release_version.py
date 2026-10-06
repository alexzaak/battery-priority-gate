"""Prevent publishing a release whose tag and HA manifest disagree."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'check_release_version.py'


class ReleaseVersionTest(unittest.TestCase):
    def run_check(self, tag, version):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'manifest.json'
            manifest.write_text(json.dumps({'version': version}))
            return subprocess.run([sys.executable, str(SCRIPT), tag, str(manifest)],
                                  capture_output=True, text=True, cwd=ROOT)

    def test_matching_tag_is_accepted(self):
        result = self.run_check('v0.3.1', '0.3.1')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_mismatched_tag_is_rejected(self):
        result = self.run_check('v0.3.0', '0.2.0')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('mismatch', result.stderr)

    def test_invalid_tag_is_rejected(self):
        result = self.run_check('release-test', '0.3.1')
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
