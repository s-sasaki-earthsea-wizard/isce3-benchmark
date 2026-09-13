"""Per-pixel validMask consistency + legacy-fallback repro for the PR #379 A/B.

Usage (from isce3-benchmark/, after the A/B runs of this bundle), with
the #359 test module copied next to this script as mask_tests.py:

    docker compose run --rm -T -v <ov_pr379>:/tmp/ov:ro \
      -v <dir-with-this-script>:/percheck -v <ab>/pr379:/products:ro \
      dev bash -c 'export PYTHONPATH=/tmp/ov:/percheck:$PYTHONPATH; \
                   cd /percheck && python3 check_validmask_percheck.py'

Part 1 reproduces the uint8/missing-dataset fallback on a minimal
all-valid grid (mask = 11 everywhere; extract_pol_valid_mask gives
HH=3, HV/VH/VV=0). Part 2 checks, per pixel, that HH validMask equals
the sub-swath validity bits of the co-located mask on all four
RIFG/RUNW radar-grid layers. Contributed during the VECR review round
(2026-09-13); output recorded in validmask_percheck_output.txt.
"""

from pathlib import Path
import tempfile

import h5py
import numpy as np

import mask_tests as fixtures
from nisar.products.insar.utils import extract_pol_valid_mask, generate_insar_mask


with tempfile.TemporaryDirectory() as directory:
    directory = Path(directory)
    lines, samples = 3, 4
    full = np.tile(np.array([[0, samples]], dtype=np.int32), (lines, 1))
    swath = fixtures.FakeSwath(
        lines, samples, fixtures.isce3.product.SubSwaths(lines, samples, [full])
    )
    zeros = np.zeros((lines, samples), dtype=np.float64)
    range_path, azimuth_path = directory / "range.off", directory / "azimuth.off"
    fixtures.make_offset_raster(range_path, zeros)
    fixtures.make_offset_raster(azimuth_path, zeros)
    for label, exception_mask in (
        ("legacy_uint8", np.zeros((lines, samples), dtype=np.uint8)),
        ("missing_dataset", None),
    ):
        with fixtures.make_h5("A", exception_mask) as ref_h5:
            with fixtures.make_h5("A", exception_mask) as sec_h5:
                mask, pol_mask = generate_insar_mask(
                    fixtures.FakeSLC(swath), fixtures.FakeSLC(swath),
                    ref_h5, sec_h5, str(range_path), str(azimuth_path), "A",
                    np.arange(lines, dtype=np.float64),
                    np.arange(samples, dtype=np.float64),
                )
                values = {
                    pol: np.unique(extract_pol_valid_mask(pol_mask, pol)).tolist()
                    for pol in ("HH", "HV", "VH", "VV")
                }
                print(label, "mask", np.unique(mask).tolist(), values)

for product in ("RIFG", "RUNW"):
    with h5py.File(f"/products/scratch/{product}.h5", "r") as h5:
        for group_name in ("interferogram", "pixelOffsets"):
            group = h5[f"/science/LSAR/{product}/swaths/frequencyA/{group_name}"]
            mismatches = 0
            both_valid = 0
            either_valid = 0
            for start in range(0, group["mask"].shape[0], 256):
                digits = group["mask"][start:start + 256] & np.uint32(0xFF)
                expected = (
                    ((digits // 10 > 0).astype(np.uint8) << 1)
                    | (digits % 10 > 0).astype(np.uint8)
                )
                actual = group["HH/validMask"][start:start + 256]
                mismatches += int(np.count_nonzero(actual != expected))
                both_valid += int(np.count_nonzero(actual == 3))
                either_valid += int(np.count_nonzero(actual != 0))
            print(product, group_name, "mismatches", mismatches,
                  "both_valid", both_valid, "either_valid", either_valid)
            assert mismatches == 0
