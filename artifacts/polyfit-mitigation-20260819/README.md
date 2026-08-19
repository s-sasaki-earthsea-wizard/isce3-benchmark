# polyfit mitigation comparison — confirmatory artifacts (2026-08-19)

One-shot confirmatory run of the pre-registered protocol
[`docs/polyfit-mitigation-prereg.md`](../../docs/polyfit-mitigation-prereg.md)
(frozen `d234d13`, amendment A1 `a94f474`). Companion report:
[`reports/2026-08-polyfit-mitigation-comparison.md`](../../reports/2026-08-polyfit-mitigation-comparison.md).

Environment: pinned dev container, isce3 0.26.0-dev at `2919e1c97`,
numpy 1.26.4, OMP/OpenBLAS/MKL threads = 1 (recorded per file).

Contents:

- `summary.json` — aggregated statistics + pre-registered gate
  evaluation (`scripts/aggregate_mitigation_results.py`).
- `frontier.md` — compact frontier table.
- `real40k.json` — recorded 40k case study (base + recorded driver
  flip per candidate). The input npz is not redistributed (see
  isce3#351); its provenance is inside the file.
- `per-seed-json.tar.gz` — all 400 per-seed JSONs:
  `confirmatory/` (seeds 1000–1199) and the four exploratory
  `robustness_*/` cells (seeds 1200–1249 each). Unpack in place to
  re-run the aggregator.
- `SHA256SUMS` — hashes of the three files above.

Provenance note: `generator_commit` spans `6350809` (31 clean / 41
dirty records) and `ee802b2` (323 clean / 5 dirty) — the results
aggregator and its tests were committed in the same worktree during
the run. The *tracked* generator/protocol blobs are identical across
both HEADs (blob IDs in `summary.json`'s
`provenance_distribution`), but the 46 `worktree_dirty=true` records
carry no dirty paths or diffs, so their exact worktree states cannot
be reconstructed. Treat this archive as a single scheduled run with
identical tracked generating inputs, not as a fully clean,
commit-pinned run. The distribution covers the 400 seed records;
`real40k.json` carries its own provenance block.
