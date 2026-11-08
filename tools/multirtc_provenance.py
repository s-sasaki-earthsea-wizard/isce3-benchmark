"""Provenance of the MultiRTC install a tool actually imported.

The MultiRTC tools here run against one of two installs, chosen by PYTHONPATH:

* the read-only pin, ``make multirtc-setup`` (v0.5.4, a0edba8), and
* the same commit with ``patches/multirtc/sicd-rgzero-radar-grid.patch``
  applied, ``make multirtc-patched-setup``.

A hard-coded commit SHA cannot tell the two apart, so this records what was
imported: the package directory, a hash of its ``*.py`` files, and the state
of the source checkout it was installed from (``MULTIRTC_CHECKOUT``).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

MULTIRTC_BASE_SHA = "a0edba80a05c923b03ffae378e4a1faf293b0f0f"
DEFAULT_CHECKOUT = "/data/external/MultiRTC-a0edba8"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(checkout: str, *args: str) -> bytes:
    return subprocess.run(["git", "-C", checkout, *args], capture_output=True, check=True).stdout


def package_tree_sha256(pkg_dir: Path) -> str:
    """sha256 of ``sha256sum``-style lines for ``multirtc/*.py``, sorted by file name."""
    lines = "".join(f"{_sha256(p.read_bytes())}  multirtc/{p.name}\n"
                    for p in sorted(pkg_dir.glob("*.py")))
    return _sha256(lines.encode())


def multirtc_provenance() -> dict:
    """Describe the imported ``multirtc`` package and the checkout it came from.

    Returns:
        ``multirtc_sha`` (the base commit, kept for older readers of run.json),
        the imported package path, the package tree and ``sicd.py`` hashes, the
        checkout's HEAD and the sha256 of its uncommitted diff ("clean" when
        there is none).
    """
    import multirtc

    pkg = Path(multirtc.__file__).resolve().parent
    checkout = os.environ.get("MULTIRTC_CHECKOUT", DEFAULT_CHECKOUT)
    out = {
        "multirtc_sha": MULTIRTC_BASE_SHA,
        "multirtc_package": str(pkg),
        "multirtc_tree_sha256": package_tree_sha256(pkg),
        "multirtc_sicd_py_sha256": _sha256((pkg / "sicd.py").read_bytes()),
        "multirtc_checkout_path": checkout,
    }
    try:
        out["multirtc_checkout"] = _git(checkout, "rev-parse", "HEAD").decode().strip()
        diff = _git(checkout, "diff", "HEAD")
        out["multirtc_checkout_diff_sha256"] = _sha256(diff) if diff else "clean"
    except Exception as exc:  # noqa: BLE001 -- provenance only
        out["multirtc_checkout"] = f"unavailable: {exc}"
    return out
