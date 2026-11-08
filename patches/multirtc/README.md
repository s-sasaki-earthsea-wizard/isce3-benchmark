# MultiRTC patches under test

Candidate upstream fixes for [MultiSAR/MultiRTC](https://github.com/MultiSAR/MultiRTC)
(BSD-3-Clause), kept here as diffs so a measurement can name exactly what it ran.
Nothing else from MultiRTC is vendored into this repository.

## `sicd-rgzero-radar-grid.patch`

`git format-patch` series of three commits on top of `a0edba80` (v0.5.4), one per
radar-grid defect found in the Capella cross-check
(`reports/2026-09-capella-rtc-gcov-vs-multirtc.md` §3.1):

| commit | file / line at a0edba80 | change |
|---|---|---|
| `adfac02` | `src/multirtc/sicd.py:42` | centre frequency `(Min + Max) / 2` (was `Min + Max / 2`) |
| `67ab961` | `src/multirtc/sicd.py:169`, `:175` | last column time at `NumCols - 1`; PRF over `NumCols - 1` intervals |
| `e7aa57b` | `src/multirtc/sicd.py:173` | starting range `R_CA_SCP - (SCPPixel.Row - FirstRow) * Row.SS` |

Each commit also adds a `CHANGELOG.md` line. The series is branch
`fix/sicd-rgzero-radar-grid` of
[s-sasaki-earthsea-wizard/MultiRTC](https://github.com/s-sasaki-earthsea-wizard/MultiRTC/tree/fix/sicd-rgzero-radar-grid)
(head `e7aa57b`). sha256 of the file:
`afcc490787a371e9372a261d9de437ccf5ed861089e354b62a82cc5e3b33849a`.

The 2026-11-08 measurement ran an earlier export of the same three commits
(file sha256 `4763a54f…`) whose messages still carried an AI-assistance
trailer. The trailers were removed before the upstream push, but the trees are
identical commit for commit. Applying either file to `a0edba80` gives a
checkout whose `git diff HEAD` has sha256
`e54cf4a3c177e0f7e858b9f07432f78d2046001df6b6fa77069ce50e06d89546`, the value
recorded in `artifacts/multirtc-sicd-fix-20261108/multirtc_run_patched-matched.json`.

`make multirtc-patched-setup` clones `a0edba80`, `git apply`s the series and
installs the result (with the same pinned sarpy) into
`data/external/multirtc-site-patched`; `make capella-multirtc-patched` and
`make multirtc-grid-survey-patched` run against it. The verification is in
`reports/2026-11-multirtc-sicd-grid-fix.md`.
