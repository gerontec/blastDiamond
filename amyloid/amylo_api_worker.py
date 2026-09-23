#!/usr/bin/env python3
"""Arbeitet die AmyloDeep-Auftraege der API (amyl_job) nacheinander ab.

Der Nutzen gegenueber Einzelaufrufen: die Modelle (ESM-2 150M/650M, UniRep, SVM, XGBoost) werden
einmal geladen und bleiben auf der Tesla P4. Ein Aufruf von amylo_gpu.py zahlt diese Ladezeit jedes
Mal; hier faellt sie nur beim Start an.

Ergebnisse gehen in dieselben Tabellen wie die Laeufe von Hand: je Job ein amyl_lauf, die Sequenz in
amyl_sequenz (einmal je Kandidat und SHA1), Werte in amyl_ergebnis/amyl_fenster.

    systemctl --user status amylo-api-worker      bzw.  systemd-Unit amylo-api-worker.service
Env: ~/iver_sim/mamba/envs/iver (CUDA 11.8, PyTorch 2.5.1)."""
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import amylo_gpu as a                      # GPUPredictor, aus_nr, kandidat_id, sequenz_id, SCHEMA_DATEI

import amylodeep
import torch
from amylodeep import load_models_and_calibrators

sys.path.insert(0, os.path.expanduser("~/iver_sim"))
import iv_db  # noqa: E402

MAX_LAENGE = 5000          # Fenster = Laenge - window + 1; 5000 aa sind ~8 Minuten auf der P4
LEERLAUF = 5               # Sekunden zwischen zwei Blicken in die Warteschlange


def log(msg):
    print(time.strftime("[%F %T] ") + msg, flush=True)


def db():
    return iv_db.conn().cursor()


def sequenz_holen(c, job):
    """(kandidat_id, acc, quelle, sequenz) fuer einen Auftrag."""
    job_id, acc, von, bis, query, w = job
    if acc:
        nr = a.aus_nr(acc)
        if not nr:
            raise RuntimeError(f"Accession {acc} nicht in der lokalen nr")
        vacc, seq = nr
        return a.kandidat_id(c, acc_basis=acc.split(".")[0]), vacc, "nr", seq
    seq = "".join(query.split()).upper()
    if not seq.isalpha():
        raise RuntimeError("Sequenz enthaelt ungueltige Zeichen")
    return a.kandidat_id(c, name=f"api {job_id[:8]}"), None, "api", seq


def job_rechnen(c, pred, lauf_sw, job):
    job_id, acc, von, bis, query, w = job
    kid, vacc, quelle, seq = sequenz_holen(c, job)
    von = von or 1
    bis = min(bis or len(seq), len(seq))
    if bis - von + 1 < w:
        raise RuntimeError(f"Bereich {von}-{bis} kuerzer als das Fenster ({w})")
    if bis - von + 1 > MAX_LAENGE:
        raise RuntimeError(f"Bereich {von}-{bis} laenger als {MAX_LAENGE} Reste")

    t0 = time.time()
    r = pred.rolling_window_prediction(seq[von - 1:bis], w)
    dt = round(time.time() - t0, 2)
    rows = [(von + pos, float(p)) for pos, p in r["position_probs"]]
    best = max(rows, key=lambda x: x[1])

    c.connection.begin()
    c.execute("INSERT INTO amyl_lauf (gestartet, sekunden, host, geraet, software, window_size, n_seq, bemerkung) "
              "VALUES (NOW(), %s, %s, %s, %s, %s, 1, %s)",
              (dt, socket.gethostname(), pred.geraet, lauf_sw, w, f"API-Auftrag {job_id}"))
    lauf = c.lastrowid
    sid = a.sequenz_id(c, kid, vacc, quelle if quelle != "nr" else "nr 2026-09-16", seq)
    c.execute("INSERT INTO amyl_ergebnis (lauf_id, seq_id, bewertet_von, bewertet_bis, avg_prob, max_prob, "
              "max_pos, sekunden) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
              (lauf, sid, von, bis, float(r["avg_probability"]), best[1], best[0], dt))
    c.executemany("INSERT INTO amyl_fenster (lauf_id, seq_id, pos, prob) VALUES (%s,%s,%s,%s)",
                  [(lauf, sid, pos, p) for pos, p in rows])
    c.execute("UPDATE amyl_job SET status='done', fertig=NOW(), sekunden=%s, lauf_id=%s, seq_id=%s "
              "WHERE job_id=%s", (dt, lauf, sid, job_id))
    c.connection.commit()
    log(f"{job_id}: {len(rows)} Fenster in {dt}s, max {best[1]:.4f} bei {best[0]} (lauf {lauf}, seq {sid})")


def main():
    c = db()
    a.sql_datei(c, a.SCHEMA_DATEI)          # einmal beim Start, nicht je Auftrag
    c.execute("UPDATE amyl_job SET status='queued', gestartet=NULL WHERE status='running'")   # nach Absturz
    log("Modelle laden ...")
    t0 = time.time()
    models, cal, tok = load_models_and_calibrators()
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    pred = a.GPUPredictor(models, cal, tok, dev)
    pred.geraet = torch.cuda.get_device_name(0) if dev == "cuda" else "cpu"
    sw = (f"amylodeep {amylodeep.__version__}, torch {torch.__version__}, cuda {torch.version.cuda}, "
          f"amylo_api_worker.py")
    log(f"bereit auf {pred.geraet} nach {time.time() - t0:.1f}s")

    while True:
        try:
            c.connection.ping(reconnect=True)
            c.execute("SELECT job_id, acc, bereich_von, bereich_bis, query, window_size FROM amyl_job "
                      "WHERE status='queued' ORDER BY erstellt LIMIT 1")
            job = c.fetchone()
            if not job:
                time.sleep(LEERLAUF)
                continue
            c.execute("UPDATE amyl_job SET status='running', gestartet=NOW() WHERE job_id=%s AND status='queued'",
                      (job[0],))
            if not c.rowcount:                      # ein anderer Worker war schneller
                continue
            log(f"{job[0]}: acc={job[1]} bereich={job[2]}-{job[3]} w={job[5]}")
            try:
                job_rechnen(c, pred, sw, job)
            except Exception as e:
                try:
                    c.connection.rollback()
                except Exception:
                    pass
                c.connection.ping(reconnect=True)
                c.execute("UPDATE amyl_job SET status='failed', fertig=NOW(), error=%s WHERE job_id=%s",
                          (f"{type(e).__name__}: {e}"[:2000], job[0]))
                log(f"{job[0]}: FEHLER {type(e).__name__}: {e}")
        except Exception as e:                      # DB weg o.ae.: warten und weiter
            log(f"Schleifenfehler: {type(e).__name__}: {e}")
            time.sleep(30)


if __name__ == "__main__":
    main()
