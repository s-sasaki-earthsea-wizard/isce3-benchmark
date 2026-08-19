"""Shared test setup: make tools/ and scripts/ importable.

Adds ``tools/`` and ``scripts/`` to sys.path so tests import the
modules directly (``from parse_insar_timing import ...``,
``from polyfit_sensitivity import ...``).
"""

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "tools"))
sys.path.insert(0, str(_ROOT / "scripts"))
