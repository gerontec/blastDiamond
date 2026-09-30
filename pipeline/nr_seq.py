#!/usr/bin/env python3
"""Sequenz zu einer nr-Accession: DIAMOND-Datenbank (dmnd_getseq), bei X-Maskierung Original von NCBI.

makedb hat Bereiche niedriger Komplexitaet zu X maskiert, blast_mask gibt es nicht. Steckt ein X in der Sequenz,
oder kennt blast_acc die Accession nicht, kommt das Original per efetch von NCBI (wenige kB, Frist 40 s).

  nr_seq.py P02766.1 [--range 20-70]     Residuen auf stdout (1-basiert, beide Enden inklusiv), Exit 1 = nicht gefunden
Als Modul: aus_nr(acc) -> (versionierte Accession, Sequenz) oder None. Env NR_DMND (Vorgabe /home/gh/diamond/nr_full.dmnd)."""
import os
import sys
import time

HIER = os.path.dirname(os.path.abspath(__file__))
os.environ["HOME"] = os.path.dirname(HIER)   # blast_db.cfg liest ~/mqtt-listener.py; als www-data (API) ist ~ sonst /var/www
os.environ.setdefault("NR_DMND", "/home/gh/diamond/nr_full.dmnd")   # vor dem Import von dmnd_getseq
sys.path.insert(0, HIER)
import blast_db   # noqa: E402  (Zugang blast_*)
import dmnd_getseq as g   # noqa: E402  (Direktzugriff auf die .dmnd)
import pymysql   # noqa: E402

_NR = {}


def aus_nr(acc):
    """(versionierte Accession, Sequenz) aus der DIAMOND-nr, oder None."""
    if "c" not in _NR:
        _NR["d"] = g.Dmnd()
        _NR["c"] = pymysql.connect(**blast_db.cfg())
        _NR["lk"] = g.luecken(_NR["c"].cursor())
    _NR["c"].ping(reconnect=True)             # der API-Worker laeuft Tage
    c = _NR["c"].cursor()
    # versionierte Accession wie blastdbcmd: exakt, sonst kleinste Version zum Praefix
    c.execute("SELECT oid, acc FROM blast_acc WHERE acc = %s LIMIT 1", (acc,))
    r = c.fetchone()
    if not r and "." not in acc:
        c.execute("SELECT oid, acc FROM blast_acc WHERE acc LIKE %s ORDER BY acc LIMIT 1", (acc + ".%",))
        r = c.fetchone()
    if not r:
        ncbi = ncbi_efetch(acc)
        if not ncbi:
            print(f"{acc}: nicht in nr und nicht bei NCBI, uebersprungen", file=sys.stderr)
        return ncbi
    oid, vacc = r
    _, seq = _NR["d"].satz(g.satznummer(oid, _NR["lk"]))
    try:
        seq = g.flicken(c, oid, seq)
    except pymysql.err.ProgrammingError:      # blast_mask fehlt
        pass
    if "X" in seq:
        ncbi = ncbi_efetch(vacc)
        if ncbi and len(ncbi[1]) == len(seq) and all(m == "X" or m == o for m, o in zip(seq, ncbi[1])):
            return vacc, ncbi[1]
        print(f"{vacc}: WARNUNG -- maskierte Bereiche (X), NCBI-Original fehlt oder passt nicht; Sequenz nicht original",
              file=sys.stderr)
    return vacc, seq.upper()


def ncbi_efetch(acc, frist=40):
    """(versionierte Accession, Sequenz) von NCBI (eutils efetch), oder None. Abbruch nach `frist` Sekunden."""
    import urllib.request
    url = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=protein&rettype=fasta&retmode=text&id="
           + acc)
    try:
        time.sleep(0.4)                       # 3 Anfragen/s ohne API-Key
        zeilen = urllib.request.urlopen(url, timeout=frist).read().decode().strip().split("\n")
    except Exception as e:
        print(f"{acc}: NCBI efetch fehlgeschlagen ({e})", file=sys.stderr)
        return None
    if not zeilen or not zeilen[0].startswith(">"):
        return None
    kopf = zeilen[0][1:].split()[0]           # gb|..., sp|P02766.1|TTHY_HUMAN oder YP_009724390.1
    vacc = next((t for t in kopf.split("|") if "." in t and t.split(".")[0].replace("_", "").isalnum()), kopf)
    return vacc, "".join(zeilen[1:]).upper()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="nr-Sequenz zu einer Accession")
    p.add_argument("acc")
    p.add_argument("--range", dest="bereich", default="", help="von-bis, 1-basiert")
    a = p.parse_args()
    r = aus_nr(a.acc)
    if not r:
        sys.exit(1)
    print(g.ausschnitt(r[1], a.bereich))
