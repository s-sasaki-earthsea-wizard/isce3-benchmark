"""Did the 2026-11-08 host reproduce the committed 2026-09-24 MultiRTC results? (stdout)

Run from the repository root on the host, after make capella-multirtc,
capella-rtc-compare and a stock grid survey into
data/capella_mexico_city/survey_stock_rerun.json:

    python3 artifacts/multirtc-sicd-fix-20261108/repro_check.py

Compares field by field (exact equality of every JSON leaf):

1. data/capella_mexico_city/rtc_compare/20240626_<variant>.json against
   artifacts/sicd-capella-rtc-20260924/20240626_<variant>.json for the four
   stock-install variants;
2. the stock grid survey against artifacts/multirtc-grid-survey-20260924/survey.json
   (scenes only; ``env`` gained provenance keys).
"""
import json

OLD_RTC = "artifacts/sicd-capella-rtc-20260924"
NEW_RTC = "data/capella_mexico_city/rtc_compare"
VARIANTS = ("stock", "matched", "matched-tfix", "matched-tfix-rfix")


def flat(d, p=""):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(flat(v, f"{p}{k}."))
    elif isinstance(d, list):
        for i, v in enumerate(d):
            out.update(flat(v, f"{p}{i}."))
    else:
        out[p[:-1]] = d
    return out


def report(name, a, b):
    shared = set(a) & set(b)
    differ = sorted(k for k in shared if a[k] != b[k])
    print(f"{name}: {len(shared)} shared fields, {len(differ)} differ"
          + (f" {differ}" if differ else "")
          + (f"; only in rerun: {sorted(set(b) - set(a))}" if set(b) - set(a) else "")
          + (f"; only in committed: {sorted(set(a) - set(b))}" if set(a) - set(b) else ""))


for v in VARIANTS:
    report(f"rtc_compare {v}", flat(json.load(open(f"{OLD_RTC}/20240626_{v}.json"))),
           flat(json.load(open(f"{NEW_RTC}/20240626_{v}.json"))))
report("grid survey (stock) scenes",
       flat(json.load(open("artifacts/multirtc-grid-survey-20260924/survey.json"))["scenes"]),
       flat(json.load(open("data/capella_mexico_city/survey_stock_rerun.json"))["scenes"]))
