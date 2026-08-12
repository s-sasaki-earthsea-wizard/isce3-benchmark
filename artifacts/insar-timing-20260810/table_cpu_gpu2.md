| stage | CPU | GPU-5080 | CPU/GPU-5080 |
|---|---|---|---|
| bandpass_insar | 0.1 | 0.1 | 1.09x |
| rdr2geo | 991.3 | 265.9 | 3.73x |
| geo2rdr | 183.5 | 128.3 | 1.43x |
| prepare_insar_hdf5 | 557.4 | 562.5 | 0.99x |
| resample | 327.5 | 183.1 | 1.79x |
| dense_offsets | 608.7 | 164.8 | 3.69x |
| polyfit rubbersheet | 218.3 | 407.6 | 0.54x |
| resample #2 | 497.9 | 472.2 | 1.05x |
| crossmul | 542.3 | 209.0 | 2.59x |
| phase unwrapping | 1441.9 | 1090.1 | 1.32x |
| └ crossmul #2 | 514.7 | 184.7 | 2.79x |
| split_spectrum | 0.0 | 0.0 | - |
| Ionosphere | 92.7 | 65.8 | 1.41x |
| └ prepare_insar_hdf5 #2 | 189.8 | 89.2 | 2.13x |
|   └ rdr2geo #2 | 126.3 | 40.3 | 3.13x |
|   └ geo2rdr #2 | 25.2 | 10.4 | 2.43x |
| └ resample #3 | 62.5 | 44.6 | 1.40x |
| └ crossmul #3 | 48.0 | 23.0 | 2.08x |
| └ phase unwrapping #2 | 56.0 | 41.7 | 1.34x |
| geocode | 214.3 | 81.0 | 2.65x |
| geocode #2 | 41.1 | 32.0 | 1.29x |
| solid earth tides | 15.5 | 13.0 | 1.19x |
| baseline | 26.3 | 30.3 | 0.87x |
| INSAR | 6175.7 | 3959.7 | 1.56x |

- CPU: self-time sum 6115.2 s vs INSAR 6175.7 s -> 60.5 s unattributed (0.98 %)
- GPU-5080: self-time sum 3904.3 s vs INSAR 3959.7 s -> 55.4 s unattributed (1.40 %)
