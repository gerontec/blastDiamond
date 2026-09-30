#!/usr/bin/env python3
"""Fuellt blast_seq_ncbi.createdate mit dem NCBI-Anlagedatum (esummary createdate).

Je Sequenz (oid) wird das frueheste createdate ihrer Accessions gespeichert. ncbi_datum bleibt
unberuehrt: das ist das LOCUS-Datum aus der Tagesdatei, also die letzte Aenderung, nicht die Anlage.

  blast_createdate_fill.py [--tage 60] [--limit N] [--workers 5] [--frist-min 0] [--von-vorn]

--limit begrenzt die Zahl der Sequenzen (Probelauf), --frist-min bricht nach N Minuten sauber ab.
Der Fortschritt steht in ~/.blast_createdate.state (hoechste erledigte oid) -- ein erneuter Aufruf
macht dort weiter. Log: ~/python/blast_createdate.log
"""
import argparse, json, os, sys, threading, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/home/gh/python")
import blast_db, pymysql

STATE = os.path.expanduser("~/.blast_createdate.state")
URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
STAPEL = 500                                   # Accessions je esummary-Anfrage
DROSSEL = threading.Semaphore(1)               # NCBI ohne Schluessel: hoechstens 3 Anfragen/s
LETZTE = [0.0]


def log(m):
    zeile = time.strftime("[%F %T] ") + m
    print(zeile, flush=True)
    with open(os.path.expanduser("~/python/blast_createdate.log"), "a") as f:
        f.write(zeile + "\n")


def esummary(accs):
    """-> {accession_ohne_version: 'YYYY/MM/DD'}; bei Fehler leeres dict."""
    data = urllib.parse.urlencode({"db": "protein", "id": ",".join(accs), "retmode": "json"}).encode()
    for versuch in (1, 2, 3):
        with DROSSEL:                                      # Anfragen mindestens 0,4 s auseinander
            warte = 0.4 - (time.time() - LETZTE[0])
            if warte > 0:
                time.sleep(warte)
            LETZTE[0] = time.time()
        try:
            js = json.loads(urllib.request.urlopen(URL, data, timeout=90).read())
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            log(f"  esummary Versuch {versuch} fehlgeschlagen: {e}")
            time.sleep(3 * versuch)
            continue
        out = {}
        for uid in js.get("result", {}).get("uids", []):
            d = js["result"][uid]
            cd = d.get("createdate")
            if cd:
                for k in (d.get("accessionversion"), d.get("caption")):
                    if k:
                        out[k.split(".")[0]] = cd
        return out
    return {}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tage", type=int, default=60)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--workers", type=int, default=5)
    p.add_argument("--frist-min", type=float, default=0)
    p.add_argument("--von-vorn", action="store_true")
    a = p.parse_args()

    ab_oid = 0 if a.von_vorn else (int(open(STATE).read().strip()) if os.path.exists(STATE) else 0)
    cn = pymysql.connect(**blast_db.cfg(), autocommit=True)
    c = cn.cursor()
    c.execute("SELECT MIN(oid), MAX(oid), COUNT(*) FROM blast_seq_ncbi WHERE ncbi_datum >= "
              "DATE_SUB(CURDATE(), INTERVAL %s DAY) AND createdate IS NULL AND oid > %s", (a.tage, ab_oid))
    lo, hi, offen = c.fetchone()
    log(f"Start: {offen} Sequenzen offen (letzte {a.tage} Tage), oid {lo}..{hi}, ab oid {ab_oid}, "
        f"{a.workers} Arbeiter, Frist {a.frist_min or '-'} min")
    if not offen:
        return

    c.execute("SELECT n.oid, a.acc FROM blast_seq_ncbi n JOIN blast_acc a ON a.oid = n.oid "
              "WHERE n.ncbi_datum >= DATE_SUB(CURDATE(), INTERVAL %s DAY) AND n.createdate IS NULL "
              "AND n.oid > %s ORDER BY n.oid" + (" LIMIT %s" if a.limit else ""),
              (a.tage, ab_oid) + ((a.limit * 4,) if a.limit else ()))
    paare = c.fetchall()
    joid = {}
    for oid, acc in paare:
        joid.setdefault(oid, []).append(acc)
    oids = sorted(joid)[:a.limit or None]
    log(f"{len(oids)} Sequenzen mit {sum(len(joid[o]) for o in oids)} Accessions geladen")

    t0 = time.time()
    haken = [0, 0, 0]                                      # erledigt, mit Datum, Accessions gefragt
    sperre = threading.Lock()

    def block(teil):
        accs, zuord = [], {}
        for oid in teil:
            for acc in joid[oid]:
                accs.append(acc)
                zuord.setdefault(acc.split(".")[0], []).append(oid)
        gef = esummary(accs)
        neu = {}
        for basis, cd in gef.items():
            for oid in zuord.get(basis, []):
                d = cd.replace("/", "-")
                if oid not in neu or d < neu[oid]:
                    neu[oid] = d
        return teil, neu, len(accs)

    # Sequenzen so buendeln, dass je Anfrage etwa STAPEL Accessions zusammenkommen
    bloecke, akt, n = [], [], 0
    for oid in oids:
        akt.append(oid)
        n += len(joid[oid])
        if n >= STAPEL:
            bloecke.append(akt)
            akt, n = [], 0
    if akt:
        bloecke.append(akt)
    log(f"{len(bloecke)} Anfragen zu je ~{STAPEL} Accessions")

    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for teil, neu, n_acc in pool.map(block, bloecke):
            if neu:
                c.executemany("UPDATE blast_seq_ncbi SET createdate=%s WHERE oid=%s",
                              [(d, o) for o, d in neu.items()])
            with sperre:
                haken[0] += len(teil)
                haken[1] += len(neu)
                haken[2] += n_acc
                if haken[0] % 5000 < len(teil):
                    dt = time.time() - t0
                    log(f"  {haken[0]}/{len(oids)} Sequenzen, {haken[1]} mit Datum "
                        f"({100*haken[1]/max(haken[0],1):.0f}%), {haken[2]/dt:.0f} Accessions/s, "
                        f"Rest ~{(len(oids)-haken[0])/(haken[0]/dt)/60:.0f} min")
            with open(STATE, "w") as f:
                f.write(str(max(teil)))
            if a.frist_min and time.time() - t0 > a.frist_min * 60:
                log("Frist erreicht -- Abbruch, der naechste Aufruf macht weiter")
                break

    dt = time.time() - t0
    log(f"Ende: {haken[0]} Sequenzen in {dt:.0f} s, {haken[1]} mit Datum "
        f"({100*haken[1]/max(haken[0],1):.0f}%), {haken[2]/dt:.0f} Accessions/s")
    c.execute("SELECT COUNT(*), COUNT(createdate), MIN(createdate), MAX(createdate) FROM blast_seq_ncbi")
    log("blast_seq_ncbi: Zeilen/mit createdate/min/max = %s" % (c.fetchone(),))


if __name__ == "__main__":
    main()
