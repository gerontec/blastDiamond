#!/usr/bin/env python3
"""Reicht alle Kandidaten einer Rolle ueber die AmyloDeep-API ein und wartet, bis sie durch sind.

Die API laesst je Schluessel nur MAX_OPEN_JOBS (3) offene Auftraege zu; dieses Skript haelt die
Warteschlange gefuellt und schiebt nach, sobald ein Platz frei wird. Ergebnisse schreibt der Worker
selbst in amyl_lauf/amyl_sequenz/amyl_ergebnis/amyl_fenster - hier wird nichts nachgetragen.

    amylo_submit.py kontrolle [--window 10] [--key-datei ~/.amylo_testkey]
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.expanduser("~/iver_sim"))
import iv_db  # noqa: E402

API = "https://yt.heissa.de/blast/api.php"
RESOLVE = ["--resolve", "yt.heissa.de:443:127.0.0.1"]      # vom dell aus: kein Umweg ueber IPv6-DNS
OFFEN_MAX = 3


def curl(args):
    r = subprocess.run(["curl", "-s", *RESOLVE, *args], capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"error": (r.stdout or r.stderr)[:200]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rolle", choices=["amyloid", "kontrolle", "einzel"])
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--key-datei", default=os.path.expanduser("~/.amylo_testkey"))
    a = ap.parse_args()
    key = open(a.key_datei).read().strip()

    c = iv_db.conn().cursor()
    c.execute("SELECT k.acc_basis, COALESCE(k.kurz, k.name) FROM amyl_kandidat k WHERE k.rolle = %s "
              "AND k.acc_basis IS NOT NULL AND NOT EXISTS (SELECT 1 FROM amyl_job j WHERE j.acc LIKE "
              "CONCAT(k.acc_basis, '%%') AND j.status IN ('queued','running','done')) ORDER BY k.kandidat_id",
              (a.rolle,))
    offen = c.fetchall()
    print(f"{len(offen)} Kandidaten der Rolle {a.rolle} ohne Auftrag", flush=True)
    if not offen:
        return

    eingereicht, fehler, t0 = 0, 0, time.time()
    for acc, name in offen:
        while True:                                     # warten, bis ein Platz frei ist
            c.connection.ping(reconnect=True)
            c.execute("SELECT COUNT(*) FROM amyl_job WHERE status IN ('queued','running')")
            if c.fetchone()[0] < OFFEN_MAX:
                break
            time.sleep(15)
        d = curl(["-X", "POST", "-H", f"X-Api-Key: {key}", "-H", "Content-Type: application/json",
                  "-d", json.dumps({"acc": acc, "window": a.window}), f"{API}?r=amylo"])
        if "job_id" in d:
            eingereicht += 1
            if eingereicht % 25 == 0 or eingereicht == 1:
                dt = time.time() - t0
                print(f"[{time.strftime('%H:%M:%S')}] {eingereicht}/{len(offen)} eingereicht, "
                      f"{dt / 60:.0f} min, zuletzt {name} ({acc})", flush=True)
        else:
            fehler += 1
            print(f"FEHLER bei {acc}: {d.get('error')}", flush=True)
            if fehler > 10:
                sys.exit("zu viele Fehler")

    print(f"{eingereicht} Auftraege eingereicht, warte auf Abschluss ...", flush=True)
    while True:
        c.connection.ping(reconnect=True)
        c.execute("SELECT status, COUNT(*) FROM amyl_job GROUP BY status")
        stand = dict(c.fetchall())
        if not stand.get("queued") and not stand.get("running"):
            break
        time.sleep(60)
    print(f"fertig nach {(time.time() - t0) / 60:.0f} min: {stand}", flush=True)


if __name__ == "__main__":
    main()
