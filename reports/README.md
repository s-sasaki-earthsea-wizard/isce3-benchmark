# Reports

Each measurement run that we cite externally (in an issue, PR, slide deck) lands
here as a dated markdown file with figures alongside in `figures/`.

## Required header for every report

```markdown
# <title>

- Date: YYYY-MM-DD
- Host: CPU / GPU / driver / CUDA toolkit
- isce3 commit: <sha>  (branch: <name>)
- isce3-benchmark commit: <sha>
- Runconfig: <path>
- Dataset: <name + DOI/URL if public>
```

The provenance file written by `scripts/_common.sh::record_provenance` into
each `logs_*/<run>/provenance.txt` has all the host/build info — copy fields
from there.

## Layout

- `YYYY-MM-DD-<topic>.md` — narrative + tables + links to figures
- `figures/YYYY-MM-DD-<topic>-<n>.png` — plots referenced from the report
- Raw `.csv` summaries belong here too if they're cited (small, OK to track)
