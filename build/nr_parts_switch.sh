#!/bin/bash
# One-time switch from nr_full.dmnd to the six nr parts (2026-11-10). Runs only when all six builds ended with
# exit 0; then: register the parts, deploy the part-aware scripts, restart the worker, delete nr_full,
# re-enable the daily delta.
set -euo pipefail
ST=/mnt/archive/gh_tmp/nr_parts_build/status.txt
NEW=/mnt/archive/gh_tmp/nr_switch            # part-aware scripts, copied here beforehand
FULL=/home/gh/diamond/nr_full.dmnd
log() { echo "$(date '+%F %T') $*"; }

for p in p1_mammalia p2_eukaryota_other p3_viruses_archaea_other p4_pseudomonadota p5_bacillati p6_bacteria_other; do
  grep -q " $p exit 0$" $ST || { log "ABORT: $p not built with exit 0"; exit 1; }
done
cd ~/python
read N L < <(python3 -c "from dmnd_getseq import Dmnd; d=Dmnd('$FULL'); print(d.n_seq, d.letters)")
log "nr_full: $N sequences, $L letters"

for f in nr_parts.py blast_api_worker.py dmnd_preselect.py nr_daily_delta.py nr_seq.py nr_acc_scan.py; do
  cp -a $f $NEW/$f.before_switch 2>/dev/null || true
  cp $NEW/$f $f
done
python3 nr_parts.py --register --n-seq $N --letters $L
python3 nr_parts.py --check

exec 9>/home/gh/.nr_dmnd.lock
flock -x 9                                   # no search reads nr_full while it goes away
sudo systemctl restart blast-api-worker
rm -f $FULL
python3 - <<'EOF'
import blast_db, pymysql
c = pymysql.connect(**blast_db.cfg(), autocommit=True).cursor()
c.execute("UPDATE blast_release SET dmnd_datei='nr parts' WHERE release_id=1")
EOF
flock -u 9
log "nr_full deleted"

crontab -l | sed 's|^# 2026-11-10 paused during the nr multi-part rebuild (nr_full is going away): ||' | crontab -
crontab -l | grep nr_daily_delta
df -h / /mnt/archive /mnt/usbparts
log "switch done"
