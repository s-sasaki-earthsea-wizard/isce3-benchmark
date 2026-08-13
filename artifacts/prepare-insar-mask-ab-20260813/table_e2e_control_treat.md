| stage | CONTROL | TREAT | CONTROL/TREAT |
|---|---|---|---|
| bandpass_insar | 0.2 | 0.1 | 4.14x |
| rdr2geo | 266.9 | 265.3 | 1.01x |
| geo2rdr | 120.4 | 180.3 | 0.67x |
| prepare_insar_hdf5 | 464.2 | 87.5 | 5.30x |
| resample | 229.7 | 219.3 | 1.05x |
| dense_offsets | 193.0 | 198.1 | 0.97x |
| polyfit rubbersheet | 406.9 | 534.6 | 0.76x |
| resample #2 | 441.6 | 452.0 | 0.98x |
| crossmul | 184.9 | 213.4 | 0.87x |
| phase unwrapping | 1077.5 | 1071.3 | 1.01x |
| └ crossmul #2 | 185.8 | 172.3 | 1.08x |
| split_spectrum | 0.0 | 0.0 | - |
| Ionosphere | 97.1 | 103.1 | 0.94x |
| └ prepare_insar_hdf5 #2 | 87.8 | 73.5 | 1.20x |
|   └ rdr2geo #2 | 41.1 | 40.9 | 1.01x |
|   └ geo2rdr #2 | 9.6 | 9.6 | 1.00x |
| └ resample #3 | 44.3 | 43.4 | 1.02x |
| └ crossmul #3 | 19.0 | 18.0 | 1.06x |
| └ phase unwrapping #2 | 47.6 | 46.9 | 1.01x |
| geocode | 80.5 | 75.5 | 1.07x |
| geocode #2 | 11.7 | 12.3 | 0.95x |
| solid earth tides | 16.1 | 16.0 | 1.00x |
| baseline | 25.4 | 26.9 | 0.94x |
| INSAR | 3865.0 | 3687.2 | 1.05x |

- CONTROL: self-time sum 3815.1 s vs INSAR 3865.0 s -> 49.9 s unattributed (1.29 %)
- TREAT: self-time sum 3637.6 s vs INSAR 3687.2 s -> 49.6 s unattributed (1.34 %)
