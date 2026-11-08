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
| `9792571` | `src/multirtc/sicd.py:42` | centre frequency `(Min + Max) / 2` (was `Min + Max / 2`) |
| `384a5b7` | `src/multirtc/sicd.py:169`, `:175` | last column time at `NumCols - 1`; PRF over `NumCols - 1` intervals |
| `e67aaa1` | `src/multirtc/sicd.py:173` | starting range `R_CA_SCP - (SCPPixel.Row - FirstRow) * Row.SS` |

Each commit also adds a `CHANGELOG.md` line. sha256 of the file:
`4763a54f739b3c3154eb8f5adde767e149575bdafd93c7f9115d6313e8849662`.

`make multirtc-patched-setup` clones `a0edba80`, `git apply`s the series and
installs the result (with the same pinned sarpy) into
`data/external/multirtc-site-patched`; `make capella-multirtc-patched` and
`make multirtc-grid-survey-patched` run against it. The verification is in
`reports/2026-11-multirtc-sicd-grid-fix.md`.
