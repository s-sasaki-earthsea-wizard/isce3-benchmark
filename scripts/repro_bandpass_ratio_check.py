#!/usr/bin/env python3
"""isce3-only reproducer: InSAR bandpass on a pair whose bands differ by a few Hz.

Two behaviours of ``isce3.splitspectrum`` (``nisar.workflows.bandpass_insar``)
met on the Capella Niscemi pairs (bench#54 reinforcement), reproduced here with
the pairs' metadata values only (no data files; a small random SLC block):

1. Trigger. ``check_range_bandwidth_overlap`` compares the two wavelengths and
   bandwidths with ``!=``. Center frequencies 26 Hz apart at 9.6 GHz (2.7e-9
   relative) and bandwidths 0.54 Hz apart are enough to bandpass the
   secondary SLC (Tukey window) to the reference band.
2. Integer-ratio check. ``bandpass_shift_spectrum`` forms the new bandwidth as
   ``(fc + bw/2) - (fc - bw/2)`` at fc ~ 9.6e9 Hz, where one ulp is 1.9e-6 Hz,
   and then requires ``rg_sample_freq % new_rg_sample_freq <= 1e-7`` (Hz,
   absolute). Whether the pair passes depends on float rounding: the
   right-looking pair passes (remainder 3e-8), the left-looking pair logs
   "Resampling scaling factor ... must be an integer." to the error channel,
   which raises ``journal.ApplicationError`` (remainder 2.3e-6).

The same arithmetic is also done here in plain Python so the remainder can be
read off without isce3.

Usage (inside the dev container)::

    python scripts/repro_bandpass_ratio_check.py
"""

from __future__ import annotations

import sys

_ARGV = sys.argv
sys.argv = [sys.argv[0]]
import isce3  # noqa: E402
from isce3.splitspectrum import splitspectrum  # noqa: E402
sys.argv = _ARGV

import numpy as np  # noqa: E402

C = isce3.core.speed_of_light
# processedCenterFrequency [Hz], processedRangeBandwidth [Hz], slantRangeSpacing [m],
# first slant range [m], as written by tools/sicd_to_nisar_rslc.py from the SICD headers
PAIRS = {
    "niscemi_ra (right-looking)": (
        (9599994496.231037, 200000114.66191864, 0.6171875, 983689.3577408057),
        (9599994470.211876, 200000115.2039852, 0.6171875, 983812.7381134487)),
    "niscemi_ld (left-looking)": (
        (9599994493.748413, 200000114.7136402, 0.6171875, 735162.0201273747),
        (9599994467.708382, 200000115.25614166, 0.6171875, 735332.6822046275)),
}


def meta(fc, bw, dr):
    """Mirror of BandpassMetaData.load_from_slc for the fields used below."""
    wvl = C / fc
    fs = C * 0.5 / dr
    if np.isclose(fs, np.round(fs), rtol=1e-8, atol=0):
        fs = float(np.round(fs))
    return {"wavelength": wvl, "rg_sample_freq": fs, "rg_bandwidth": bw, "center_freq": C / wvl}


def run_pair(name, ref, sec) -> str:
    m_ref, m_sec = meta(*ref[:3]), meta(*sec[:3])
    triggered = (m_ref["wavelength"] != m_sec["wavelength"]) or (m_ref["rg_bandwidth"] != m_sec["rg_bandwidth"])
    target_is_ref = m_ref["rg_bandwidth"] > m_sec["rg_bandwidth"]
    base, target = (m_sec, m_ref) if target_is_ref else (m_ref, m_sec)
    tgt_raw = ref if target_is_ref else sec
    ratio = base["rg_sample_freq"] / base["rg_bandwidth"]
    low = base["center_freq"] - 0.5 * base["rg_bandwidth"]
    high = base["center_freq"] + 0.5 * base["rg_bandwidth"]
    new_fs = abs(high - low) * ratio
    remainder = target["rg_sample_freq"] % new_fs
    print(f"{name}")
    print(f"  center frequency difference  {abs(ref[0] - sec[0]):.3f} Hz"
          f"  ({abs(ref[0] - sec[0]) / ref[0]:.2e} relative)")
    print(f"  bandwidth difference         {abs(ref[1] - sec[1]):.3f} Hz")
    print(f"  bandpass triggered (!=)      {triggered}  -> target = {'ref' if target_is_ref else 'sec'}")
    print(f"  (high - low) - base bw       {(high - low) - base['rg_bandwidth']:+.3e} Hz")
    print(f"  rg_sample_freq % new_fs      {remainder:.3e} Hz  (threshold 1e-7)")

    bp = splitspectrum.SplitSpectrum(
        rg_sample_freq=target["rg_sample_freq"], rg_bandwidth=target["rg_bandwidth"],
        center_frequency=target["center_freq"], slant_range=lambda i: tgt_raw[3] + i * tgt_raw[2],
        freq="A", sampling_bandwidth_ratio=ratio)
    rng = np.random.default_rng(0)
    block = (rng.standard_normal((8, 1024)) + 1j * rng.standard_normal((8, 1024))).astype(np.complex64)
    try:
        bp.bandpass_shift_spectrum(block, low, high, base["center_freq"],
                                   window_function="tukey", window_shape=0.25, resampling=True)
        result = "OK"
    except Exception as exc:  # noqa: BLE001 -- the failure is the result
        result = f"{type(exc).__name__}: {exc}"
    print(f"  isce3 bandpass_shift_spectrum  {result}\n")
    return result


def main() -> int:
    print(f"isce3 {isce3.__version__}\n")
    res = {name: run_pair(name, *vals) for name, vals in PAIRS.items()}
    ok = (res["niscemi_ra (right-looking)"] == "OK"
          and res["niscemi_ld (left-looking)"].startswith("ApplicationError"))
    print("REPRODUCED" if ok else "NOT REPRODUCED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
