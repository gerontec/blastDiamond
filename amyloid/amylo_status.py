#!/usr/bin/env python3
"""Eine Statuszeile zum AmyloDeep-Kontrolllauf: Jobs, offene Kandidaten, Einreicher, GPU.
Aufruf ohne Argumente; gedacht fuer Ueberwachungsschleifen."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.expanduser("~/python"))
import blast_db
import pymysql

c = pymysql.connect(**blast_db.cfg()).cursor()
c.execute("SELECT status, COUNT(*) FROM amyl_job GROUP BY status")
st = dict(c.fetchall())
done, failed = st.get("done", 0), st.get("failed", 0)
offen = st.get("queued", 0) + st.get("running", 0)
c.execute("SELECT COUNT(*) FROM amyl_fenster")
fenster = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM amyl_kandidat k WHERE k.rolle = 'kontrolle' AND k.acc_basis IS NOT NULL "
          "AND NOT EXISTS (SELECT 1 FROM amyl_job j WHERE j.acc LIKE CONCAT(k.acc_basis, '%') "
          "AND j.status IN ('queued','running','done'))")
rest = c.fetchone()[0]


def laeuft(muster):
    return "ok" if subprocess.run(["pgrep", "-f", muster], capture_output=True).returncode == 0 else "WEG"


temp = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu",
                       "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout.strip()
t, u = (temp.split(", ") + ["?", "?"])[:2]
print(f"done={done} failed={failed} offen={offen} rest={rest} fenster={fenster} "
      f"submit={laeuft('amylo_submit.py')} worker={laeuft('amylo_api_worker.py')} gpu={t}C/{u}%")
