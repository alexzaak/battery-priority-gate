"""Fail CI when a release tag disagrees with the integration manifest version."""
import json
import re
import sys
from pathlib import Path

DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / 'custom_components' / 'senec_marstek_gate' / 'manifest.json'


def main(args):
    if len(args) not in (1, 2) or not re.fullmatch(r'v\d+\.\d+\.\d+', args[0]):
        print('expected release tag vMAJOR.MINOR.PATCH [manifest-path]', file=sys.stderr)
        return 2
    path = Path(args[1]) if len(args) == 2 else DEFAULT_MANIFEST
    version = json.loads(path.read_text())['version']
    if args[0][1:] != version:
        print(f'release version mismatch: tag {args[0]}, manifest {version}', file=sys.stderr)
        return 1
    print(f'release version matches: {args[0]}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
