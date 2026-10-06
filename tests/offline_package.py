"""Load the pure packaged modules without importing the HA integration entry point.

The real package __init__ imports Home Assistant. Offline tests only need the
package path so Python can import gate/quality/classify from the shipped folder.
The integration-entry-point tests independently import and exercise __init__.
"""
import sys
import types
from pathlib import Path

PACKAGE = 'custom_components.senec_marstek_gate'


def prepare() -> None:
    if PACKAGE not in sys.modules:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(Path(__file__).resolve().parents[1] /
                                'custom_components' / 'senec_marstek_gate')]
        sys.modules[PACKAGE] = package
