# bench#36 Step 2 — Phase B results (FFTW_MEASURE plan + result stability)

Host: ew-s-sasaki-beacon-NucBox-EVO-T1, quiescent gate = 3 consecutive 5 s samples < 8% CPU.
Probe source: fftw_probe/fftw_plan_probe.c  (sha256 below)
Runs analysed: tag 'idle_v3', 5 runs, no synthetic load.

## Raw probe output
```
--- idle_v3 run1 ---
raw_r2c plan_hash=9154cbc32c51dc64 plan_len=494 in_hash=31a829ba482f72c4 out_hash=a9a6b1665c82f3a1
raw_c2r plan_hash=d957283a0b7254c1 plan_len=498 out_hash=252a57910c83fcea
oversampled_r2c plan_hash=1c3908dad9e388c3 plan_len=890 in_hash=a295b484a698616f out_hash=fb623717767b3413
oversampled_c2r plan_hash=edd3c3c170ae5fff plan_len=583 out_hash=0d4beab5f84c2325
--- idle_v3 run2 ---
raw_r2c plan_hash=5c483c2cd5bc9833 plan_len=494 in_hash=31a829ba482f72c4 out_hash=ede5a7de6d4a9333
raw_c2r plan_hash=8dbea6f2b12ca455 plan_len=498 out_hash=6b2c8e245d6ba46f
oversampled_r2c plan_hash=fb4d5c57a2966139 plan_len=583 in_hash=a295b484a698616f out_hash=fb623717767b3413
oversampled_c2r plan_hash=2f293ef0d8c583b1 plan_len=890 out_hash=ee527f136d77dfe7
--- idle_v3 run3 ---
raw_r2c plan_hash=86228cd0e8595e78 plan_len=498 in_hash=31a829ba482f72c4 out_hash=1bc5d6a17f037a08
raw_c2r plan_hash=8dbea6f2b12ca455 plan_len=498 out_hash=a771b3be7949052a
oversampled_r2c plan_hash=1c3908dad9e388c3 plan_len=890 in_hash=a295b484a698616f out_hash=fb623717767b3413
oversampled_c2r plan_hash=12c05a7631f25595 plan_len=890 out_hash=48eb0ea8ebce148b
--- idle_v3 run4 ---
raw_r2c plan_hash=435eaef13cbbfc48 plan_len=488 in_hash=31a829ba482f72c4 out_hash=c13c8c008230c481
raw_c2r plan_hash=d957283a0b7254c1 plan_len=498 out_hash=9689fddfdd9bc166
oversampled_r2c plan_hash=1c3908dad9e388c3 plan_len=890 in_hash=a295b484a698616f out_hash=fb623717767b3413
oversampled_c2r plan_hash=2f293ef0d8c583b1 plan_len=890 out_hash=ee527f136d77dfe7
--- idle_v3 run5 ---
raw_r2c plan_hash=9154cbc32c51dc64 plan_len=494 in_hash=31a829ba482f72c4 out_hash=a9a6b1665c82f3a1
raw_c2r plan_hash=8dbea6f2b12ca455 plan_len=498 out_hash=252a57910c83fcea
oversampled_r2c plan_hash=eee6e2879c35c6fb plan_len=890 in_hash=a295b484a698616f out_hash=2c60b18b6435038e
oversampled_c2r plan_hash=58ff8ccbe569ffac plan_len=767 out_hash=65642ede8156cddd
```

## Distinct plans vs distinct results
```
  raw_r2c            distinct plans=4  distinct outputs=4  distinct inputs=1
  raw_c2r            distinct plans=2  distinct outputs=4  distinct inputs=0
  oversampled_r2c    distinct plans=3  distinct outputs=2  distinct inputs=1
  oversampled_c2r    distinct plans=4  distinct outputs=4  distinct inputs=0
```

## Numerical spread between runs (byte-identical input)
```

# tag: idle_v3   (/home/ew-s-sasaki-beacon/scratch/bench36_step2_20260826/fftw_probe/dump_idle_v3)
  oversampled_r2c.bin: 5 runs, 2 distinct results (groups: [[1, 2, 3, 4], [5]])
    run1 vs run5: n_diff=111576/303680 (3.67e-01)  max|d|=3.052e-05  max_ulp=65536
    worst over all pairs vs run1: max|d|=3.052e-05  max_rel=4.292e-03  max_ulp=65536
  raw_r2c.bin: 5 runs, 4 distinct results (groups: [[1, 5], [2], [3], [4]])
    run1 vs run2: n_diff=97580/208000 (4.69e-01)  max|d|=2.289e-05  max_ulp=720896
    run1 vs run3: n_diff=158674/208000 (7.63e-01)  max|d|=2.289e-05  max_ulp=2097152
    run1 vs run4: n_diff=158510/208000 (7.62e-01)  max|d|=3.052e-05  max_ulp=4194304
    worst over all pairs vs run1: max|d|=3.052e-05  max_rel=2.778e-01  max_ulp=4194304
```

## Scale-relative magnitude
```
raw_r2c.bin: rms(ref)=29.21 peak(ref)=132.7 worst max|d|=3.052e-05 -> /rms=1.045e-06 /peak=2.300e-07
   first differing run: max_rel(|ref|>1%rms)=1.947e-05 median_rel=1.247e-07  (float32 eps = 1.192e-07)
oversampled_r2c.bin: rms(ref)=35.3 peak(ref)=194.4 worst max|d|=3.052e-05 -> /rms=8.646e-07 /peak=1.570e-07
   first differing run: max_rel(|ref|>1%rms)=2.906e-05 median_rel=1.122e-07  (float32 eps = 1.192e-07)
```

## Provenance
```
84c1e7cdc1b64b70a38bba31388a9af3174f3016b701d4d99cd3678abae75a5a  /home/ew-s-sasaki-beacon/scratch/bench36_step2_20260826/fftw_probe/fftw_plan_probe.c
1bf27cc3c8eda8993cf1c2d9e61af9c62ea0003801173b39654de0eae3c7eb88  /home/ew-s-sasaki-beacon/scratch/bench36_step2_20260826/fftw_probe/run_fftw_probe.sh
5bd768faa1ac8dbd080bf6e09ec3ecfe984c435f80b9b8b1db5e5da83dbbd33a  /home/ew-s-sasaki-beacon/scratch/bench36_step2_20260826/fftw_probe/compare_fftw_dumps.py
c8883a3df81b77cc7291280731d9b99f9acd117b63235326c3ec688539097ad0  /home/ew-s-sasaki-beacon/scratch/bench36_step2_20260826/PREREGISTRATION.md
b2001286a77b5b6670490b83e8019b88af39a615f4515b47357379f33ee92817  /home/ew-s-sasaki-beacon/scratch/bench36_step2_20260826/STATIC_ANALYSIS.md
```
