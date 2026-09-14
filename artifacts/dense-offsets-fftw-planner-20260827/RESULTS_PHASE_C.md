# bench#36 Step 2 — Phase C results (standalone dense_offsets, 3 arms x 3 replicates)

See PREREGISTRATION.md (frozen) and AMENDMENT_A1.md. Every number below comes
from a file in this directory.

## Input pinning (measured)
  reference.slc  identical across replicates (sha256 6b23416ae7e83b2a92bbbdb453b4b48b...)
  secondary      phase0 coarse_resample_slc, read-only mount -> identical by construction

## Wall times
  idle  omp=16 load=0: 519/564/551 s   mean 545 s
  load  omp=16 load=15: 1139/1141/1199 s   mean 1160 s
  omp1  omp=1 load=0: 701/703/614 s   mean 673 s

## Equivalence classes over all 9 runs (sha256, first 16 hex)
```
gross_offsets:
  idle  rep1  6a229ddf5e9ed2e6
  idle  rep2  6a229ddf5e9ed2e6
  idle  rep3  6a229ddf5e9ed2e6
  load  rep1  6a229ddf5e9ed2e6
  load  rep2  6a229ddf5e9ed2e6
  load  rep3  6a229ddf5e9ed2e6
  omp1  rep1  6a229ddf5e9ed2e6
  omp1  rep2  6a229ddf5e9ed2e6
  omp1  rep3  6a229ddf5e9ed2e6
  -> 1 distinct value(s) across 9 runs
snr:
  idle  rep1  0e4f704e398695d2
  idle  rep2  0e4f704e398695d2
  idle  rep3  0e4f704e398695d2
  load  rep1  0e4f704e398695d2
  load  rep2  d9ff926eaabca3dd
  load  rep3  3acea78467ce9a3b
  omp1  rep1  707380199fa6ad26
  omp1  rep2  0e4f704e398695d2
  omp1  rep3  0e4f704e398695d2
  -> 4 distinct value(s) across 9 runs
covariance:
  idle  rep1  a0087e14de321fca
  idle  rep2  a0087e14de321fca
  idle  rep3  a0087e14de321fca
  load  rep1  a0087e14de321fca
  load  rep2  d989950c8bb9c3f3
  load  rep3  73b8d368d4c060d3
  omp1  rep1  559918c0544d5b58
  omp1  rep2  a0087e14de321fca
  omp1  rep3  a0087e14de321fca
  -> 4 distinct value(s) across 9 runs
dense_offsets:
  idle  rep1  62e702e28350c437
  idle  rep2  cadb52d2db4185ca
  idle  rep3  f3c8e7181ea110d3
  load  rep1  c47519a422e57ae3
  load  rep2  7af3f8bde3d77647
  load  rep3  5585d80e169e71b5
  omp1  rep1  9bce00a36bd795b5
  omp1  rep2  97e45445a9b23313
  omp1  rep3  8097ed72cac9e757
  -> 9 distinct value(s) across 9 runs
correlation_peak:
  idle  rep1  cd498dc5b4f43371
  idle  rep2  0ee7b4c13eabf0ea
  idle  rep3  5043552aa8c15d54
  load  rep1  8f8f04c8cce121a7
  load  rep2  bda0df06250eadf9
  load  rep3  53ee04f1fad55c4c
  omp1  rep1  bb766b6d4636ab97
  omp1  rep2  57951e9f60eb0aa6
  omp1  rep3  cc494f361b34340d
  -> 9 distinct value(s) across 9 runs
```

## Within-arm and cross-arm detail
```
idle: 3 replicate(s)
load: 3 replicate(s)
omp1: 3 replicate(s)

# sha256 of Ampcor outputs
  dense_offsets      idle:3uniq/3  load:3uniq/3  omp1:3uniq/3
  gross_offsets      idle:1uniq/3  load:1uniq/3  omp1:1uniq/3
  snr                idle:1uniq/3  load:3uniq/3  omp1:2uniq/3
  covariance         idle:1uniq/3  load:3uniq/3  omp1:2uniq/3
  correlation_peak   idle:3uniq/3  load:3uniq/3  omp1:3uniq/3

# within-group detail (replicate 1 as reference)

## idle
  dense_offsets      rep1 vs rep2: DIFFERS n=46/766270 (6.00e-05) max=3.125e-02
  dense_offsets      rep1 vs rep3: DIFFERS n=38/766270 (4.96e-05) max=3.125e-02
  gross_offsets      rep1 vs rep2: identical
  gross_offsets      rep1 vs rep3: identical
  snr                rep1 vs rep2: identical
  snr                rep1 vs rep3: identical
  covariance         rep1 vs rep2: identical
  covariance         rep1 vs rep3: identical
  correlation_peak   rep1 vs rep2: DIFFERS n=323494/383135 (8.44e-01) max=7.749e-07
  correlation_peak   rep1 vs rep3: DIFFERS n=327040/383135 (8.54e-01) max=7.153e-07

## load
  dense_offsets      rep1 vs rep2: DIFFERS n=396/766270 (5.17e-04) max=6.000e+01
  dense_offsets      rep1 vs rep3: DIFFERS n=415/766270 (5.42e-04) max=6.356e+01
  gross_offsets      rep1 vs rep2: identical
  gross_offsets      rep1 vs rep3: identical
  snr                rep1 vs rep2: DIFFERS n=188534/383135 (4.92e-01) max=1.373e-04
  snr                rep1 vs rep3: DIFFERS n=251753/383135 (6.57e-01) max=1.221e-04
  covariance         rep1 vs rep2: DIFFERS n=803220/1149405 (6.99e-01) max=1.330e+02
  covariance         rep1 vs rep3: DIFFERS n=872812/1149405 (7.59e-01) max=1.772e+02
  correlation_peak   rep1 vs rep2: DIFFERS n=330164/383135 (8.62e-01) max=3.163e-01
  correlation_peak   rep1 vs rep3: DIFFERS n=327107/383135 (8.54e-01) max=3.163e-01

## omp1
  dense_offsets      rep1 vs rep2: DIFFERS n=409/766270 (5.34e-04) max=6.059e+01
  dense_offsets      rep1 vs rep3: DIFFERS n=405/766270 (5.29e-04) max=6.059e+01
  gross_offsets      rep1 vs rep2: identical
  gross_offsets      rep1 vs rep3: identical
  snr                rep1 vs rep2: DIFFERS n=250643/383135 (6.54e-01) max=1.678e-04
  snr                rep1 vs rep3: DIFFERS n=250643/383135 (6.54e-01) max=1.678e-04
  covariance         rep1 vs rep2: DIFFERS n=864451/1149405 (7.52e-01) max=1.159e+02
  covariance         rep1 vs rep3: DIFFERS n=864451/1149405 (7.52e-01) max=1.159e+02
  correlation_peak   rep1 vs rep2: DIFFERS n=316508/383135 (8.26e-01) max=2.580e-01
  correlation_peak   rep1 vs rep3: DIFFERS n=305365/383135 (7.97e-01) max=2.580e-01

# across-group (rep1 of each tag vs rep1 of first tag)

## idle vs load
  dense_offsets     : DIFFERS n=40/766270 (5.22e-05) max=3.125e-02
  gross_offsets     : identical
  snr               : identical
  covariance        : identical
  correlation_peak  : DIFFERS n=327047/383135 (8.54e-01) max=7.153e-07

## idle vs omp1
  dense_offsets     : DIFFERS n=407/766270 (5.31e-04) max=6.059e+01
  gross_offsets     : identical
  snr               : DIFFERS n=250643/383135 (6.54e-01) max=1.678e-04
  covariance        : DIFFERS n=864451/1149405 (7.52e-01) max=1.159e+02
  correlation_peak  : DIFFERS n=293735/383135 (7.67e-01) max=2.580e-01
```
