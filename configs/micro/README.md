# Single-stage micro-benchmarks

Each runconfig here exercises exactly one GPU-accelerated isce3 stage so that
profiling output points at one kernel's timeline rather than an end-to-end mix.
This format is what gets attached to upstream RFC issues as a "minimal repro".

GPU-enabled stages worth isolating (from `isce3/python/packages/isce3/cuda/`):

- `geo2rdr` / `rdr2geo`
- `resample_slc` (and v2)
- `crossmul`
- `dense_offsets` / `offsets_product`
- `geocode_insar` / `geocode_corrections`
- `baseline`

Stage runconfigs to be added here as we work through the bench plan.
