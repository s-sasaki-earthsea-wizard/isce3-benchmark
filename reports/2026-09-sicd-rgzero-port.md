# The SICD converter as an isce3 example: port and equivalence check

- Date: 2026-09-30
- isce3 fork branch: [`feat/sicd-rgzero-to-rslc`](https://github.com/s-sasaki-earthsea-wizard/isce3/tree/feat/sicd-rgzero-to-rslc)
  at `d4f92b5f5`, based on upstream `develop` `770e4d8e1` (2026-09-29). Not proposed upstream;
  it is the provisional implementation for the RFC decision recorded in bench#54.
- Build used for the checks: the bench container's isce3 `0.26.0-dev+2919e1c97`.
- Bench converter compared against: `tools/sicd_to_nisar_rslc.py` on `main` (`26ceb00`).

## What was ported

`share/nisar/examples/sicd_to_nisar_rslc.py` (1,044 lines) replaces the bench converter
(913 lines) and its two helper modules (620 lines) with one script, next to isce3's own
`alos2_to_nisar_l1.py`. Inside, three layers: NITF / SICD XML parsing, pure mapping
functions (grid, azimuth flip, orbit, attitude, radiometric LUTs) and the RSLC writer, so a
later move into a package is mechanical. It needs numpy, h5py and isce3 only.

Kept: the RGZERO / INCA mapping, the left-looking azimuth flip, the orbit from `ARPPoly`,
DN and beta0 radiometry with the noise LUT, rejection of RGAZIM and other grids by name.
Dropped: the bench-only diagnostics (spectral centroids, amplitude statistics), which stay
in the bench converter.

Tests, `tests/python/packages/nisar/examples/sicd_to_nisar_rslc.py`, 11 cases on synthetic
but geometrically consistent headers (left and right looking): grid arithmetic, SCP
round trip through isce3 within 1 mm, RGAZIM and non-linear `TimeCAPoly` rejection, beta0
and noise LUTs, and end to end through a small NITF written by the test, read back with
`nisar.products.readers.SLC`. Registered in `tests/python/packages/CMakeLists.txt`.
Result in the bench container: 11 passed.

## Equivalence with the bench converter

`scripts/check_sicd_port_equivalence.sh` converts every Capella scene on disk with both
converters, in both radiometries, and compares the two RSLCs dataset by dataset with
`tools/compare_rslc_h5.py` (values exactly, NaN equal; dtypes, shapes, attributes). Only
provenance may differ: `processingDateTime`, `processingCenter`, `inputs/converter`, two
attribute descriptions, and the bench-only `inputs/conversionDiagnostics`.

| scene | radiometry | datasets compared | differences | unexpected | port wall time |
|---|---|---|---|---|---|
| Mexico City L-A 06-26 | dn | 85 | 6, all expected | 0 | 40.5 s |
| Mexico City L-A 06-26 | beta0 | 88 | 6, all expected | 0 | 77.2 s |
| Mexico City L-A 06-29 | dn | 85 | 6, all expected | 0 | 39.6 s |
| Mexico City L-A 06-29 | beta0 | 88 | 6, all expected | 0 | 70.1 s |
| Niscemi R-A 02-04 | dn | 85 | 6, all expected | 0 | 27.1 s |
| Niscemi R-A 02-04 | beta0 | 88 | 6, all expected | 0 | 45.3 s |
| Niscemi R-A 02-07 | dn | 85 | 6, all expected | 0 | 28.4 s |
| Niscemi R-A 02-07 | beta0 | 88 | 6, all expected | 0 | 39.7 s |
| Niscemi L-D 02-04 | dn | 85 | 6, all expected | 0 | 17.7 s |
| Niscemi L-D 02-04 | beta0 | 88 | 6, all expected | 0 | 36.0 s |
| Niscemi L-D 02-07 | dn | 85 | 6, all expected | 0 | 20.8 s |
| Niscemi L-D 02-07 | beta0 | 88 | 6, all expected | 0 | 36.6 s |
| Yumare R-D 06-27 | dn | 85 | 6, all expected | 0 | 50.9 s |
| Yumare R-D 06-27 | beta0 | 88 | 6, all expected | 0 | 80.1 s |

The differing paths, identical in every case: `RSLC/metadata/processingInformation/inputs/conversionDiagnostics`, `RSLC/metadata/processingInformation/inputs/converter`, `RSLC/swaths/frequencyA/validSamplesSubSwath1@description`, `identification/hasInputDataException@description`, `identification/processingCenter`, `identification/processingDateTime`.

## What this does and does not establish

- Established: on these seven scenes, in both radiometries, the port writes the same RSLC as the
  bench converter on `main`, apart from six provenance fields that no isce3 workflow computes
  with. Results obtained from that converter's products therefore hold for the port's products
  in the same way.
- Not established: anything beyond the bench converter's own evidence; the port adds no new
  capability.

## Reproduce

```
git -C <isce3 clone> worktree add ../isce3-sicd-rgzero feat/sicd-rgzero-to-rslc
docker compose run --rm -T -v <path>/isce3-sicd-rgzero:/opt/isce3-wt:ro dev \
    bash scripts/check_sicd_port_equivalence.sh
docker compose run --rm -T -v <path>/isce3-sicd-rgzero:/opt/isce3-wt:ro dev \
    python -m pytest /opt/isce3-wt/tests/python/packages/nisar/examples/sicd_to_nisar_rslc.py
```
