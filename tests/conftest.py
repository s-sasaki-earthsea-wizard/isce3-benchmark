"""Shared test setup: make tools/ importable from the tests.

Adds ``tools/`` to sys.path so tests import the modules directly
(``from parse_insar_timing import ...``).
"""

import pathlib
import sys

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
