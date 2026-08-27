#!/bin/bash
# Block until the host is quiescent: N consecutive 5 s samples with
# system-wide CPU utilisation below THRESH percent. FFTW_MEASURE benchmarks
# candidate plans by wall clock, so the probe is only meaningful on an idle
# host (PREREGISTRATION.md, Phase B).
THRESH=${1:-8}
NEED=${2:-3}
MAXW=${3:-600}
read -r _ u n s i rest < /proc/stat; pu=$u; pn=$n; ps_=$s; pi=$i
ok=0; waited=0
while [ $ok -lt "$NEED" ] && [ $waited -lt "$MAXW" ]; do
    sleep 5; waited=$((waited+5))
    read -r _ u n s i rest < /proc/stat
    du=$((u-pu)); dn=$((n-pn)); ds=$((s-ps_)); di=$((i-pi))
    tot=$((du+dn+ds+di))
    [ "$tot" -le 0 ] && continue
    busy=$(( (du+dn+ds)*100 / tot ))
    pu=$u; pn=$n; ps_=$s; pi=$i
    if [ "$busy" -lt "$THRESH" ]; then ok=$((ok+1)); else ok=0; fi
    echo "  cpu_busy=${busy}%  quiet_streak=${ok}/${NEED}  waited=${waited}s"
done
echo "loadavg at release: $(cut -d' ' -f1-3 /proc/loadavg)"
[ $ok -ge "$NEED" ] && echo "HOST QUIESCENT" || echo "TIMEOUT — proceeding anyway (not quiescent)"
