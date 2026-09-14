# bench#36 Step 2 — Phase A results (unwrap-step replay, 3 replicates)

Seed: phase0 scratch; RIFG.h5 sha256 f926c947ba299a12ca684a2dd51aa7ab34f70d7b8e11a8f9e373d13249589e4c (read-only mount).
Replicate wall times: rep1 unwrap.run 1334.246 s; all three exited rc=0.
Comparison run inside the dev container (snaphu-py mkstemp files are root/0600).

```
# unwrap-step replicate comparison  (reps: [1, 2, 3])

scratch intermediates: 18 identical, 0 differing
  same crossmul/freqA/HH/coherence_rg13_az16
  same crossmul/freqA/HH/coherence_rg13_az16.hdr
  same crossmul/freqA/HH/reference.hdr
  same crossmul/freqA/HH/reference.slc
  same crossmul/freqA/HH/wrapped_igram_rg13_az16
  same crossmul/freqA/HH/wrapped_igram_rg13_az16.hdr
  same crossmul/product.h5
  same rdr2geo/freqA/RUNW_ifgram_dem.hdr
  same rdr2geo/freqA/RUNW_ifgram_dem.rdr
  same rdr2geo/freqA/RUNW_offsets_dem.hdr
  same rdr2geo/freqA/RUNW_offsets_dem.rdr
  same unwrap/freqA/HH/snaphu.config.txt
  same unwrap/freqA/HH/snaphu.conncomp.u4
  same unwrap/freqA/HH/snaphu.corr.f4
  same unwrap/freqA/HH/snaphu.igram.c8
  same unwrap/freqA/HH/snaphu.unw.f4
  same unwrap/freqA/HH/wrapped_igram.filt
  same unwrap/freqA/HH/wrapped_igram.hdr

  SNAPHU solver inputs : 2 identical, 0 differing
  SNAPHU solver outputs: 2 identical, 0 differing

RUNW dataset comparison (rep1 as reference):
  swaths/frequencyA/interferogram/HH/unwrappedPhase: rep2:identical, rep3:identical
  swaths/frequencyA/interferogram/HH/connectedComponents: rep2:identical, rep3:identical
  swaths/frequencyA/interferogram/HH/coherenceMagnitude: rep2:identical, rep3:identical
  swaths/frequencyA/interferogram/mask: rep2:identical, rep3:identical
  swaths/frequencyA/pixelOffsets/HH/alongTrackOffset: rep2:identical, rep3:identical
  swaths/frequencyA/pixelOffsets/HH/slantRangeOffset: rep2:identical, rep3:identical
  swaths/frequencyA/pixelOffsets/HH/correlationSurfacePeak: rep2:identical, rep3:identical
  swaths/frequencyA/pixelOffsets/digitalElevationModel: rep2:identical, rep3:identical

Statistics attributes (rep1 as reference):
  all compared attributes identical

VERDICT:
  H1 and H2 REJECTED: the unwrap step is fully deterministic on fixed inputs -> E2E flips originate upstream of unwrap. Proceed to Phase B/C.
```
