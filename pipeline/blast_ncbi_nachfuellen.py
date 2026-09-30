#!/usr/bin/env python3
"""NCBI-Datum fuer Sequenzen des nr-Stands nachtragen, die kurz vor dem Snapshot neu kamen.

blast_seq_ncbi kennt ein Datum nur fuer die per Tagesdelta angehaengten Sequenzen (ab 16.09.2026).
Was zwischen VON und dem Snapshot bei GenBank erschien, steckt im nr-Stand ohne Datum -- der
Filter neu_tage (dmnd_vorauswahl.py) saehe es nicht. Dieses Skript liest die GenBank-Tagesdateien
(daily-nc, ~180 Tage vorgehalten) im Zeitraum, sucht die Accessions in blast_acc und legt fuer
jede gefundene OID des nr-Stands eine Zeile (oid, LOCUS-Datum) an. Das Anlagedatum (createdate)
holt danach blast_createdate_fill.py --von-vorn --tage N wie gehabt per esummary -- ueber ALLE
Accessions der OID, damit eine alte Sequenz mit nur einer neuen Accession nicht als neu gilt.

  blast_ncbi_nachfuellen.py --von 2026-07-02 --bis 2026-09-15      Dateien laden und eintragen
  blast_ncbi_nachfuellen.py ... --nur-zaehlen                      nichts schreiben, nur Umfang

RefSeq-Tagesdateien reichen nur ~4 Wochen zurueck und fehlen hier.
Log: ~/python/blast_ncbi_nachfuellen.log; Dateien in WORK, fertige bekommen eine .done-Marke.
"""
import argparse, gzip, os, re, sys, time, urllib.request

sys.path.insert(0, "/home/gh/python")
import blast_db, pymysql

BASE = "https://ftp.ncbi.nlm.nih.gov/genbank/daily-nc/"
WORK = os.path.expanduser("~/nr_update/nachfuell")
LOG = os.path.expanduser("~/python/blast_ncbi_nachfuellen.log")
MON = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}


def log(m):
    z = time.strftime("[%F %T] ") + m
    print(z, flush=True)
    with open(LOG, "a") as f:
        f.write(z + "\n")


def dateien(von, bis):
    html = urllib.request.urlopen(BASE, timeout=60).read().decode()
    r = re.findall(r'href="(nc\d{4}\.gnp\.gz)">[^<]*</a>\s+(\d{4}-\d{2}-\d{2})', html)
    return sorted({(d, n) for n, d in r if von <= d <= bis})


def lesen(pfad):
    """-> [(acc.version, 'YYYY-MM-DD')] aus LOCUS/VERSION; Sequenzen werden uebersprungen."""
    out, datum = [], None
    with gzip.open(pfad, "rt", encoding="utf-8", errors="replace") as f:
        for z in f:
            if z.startswith("LOCUS"):
                t, m, j = z.split()[-1].split("-")
                datum = f"{j}-{MON[m]:02d}-{int(t):02d}"
            elif z.startswith("VERSION"):
                out.append((z.split()[1], datum))
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--von", required=True)
    p.add_argument("--bis", required=True)
    p.add_argument("--nur-zaehlen", action="store_true")
    a = p.parse_args()
    os.makedirs(WORK, exist_ok=True)
    c = pymysql.connect(**blast_db.cfg(), autocommit=True).cursor()
    c.execute("SELECT COALESCE(MIN(first_oid), 0) FROM blast_import i JOIN blast_release r ON r.release_id = i.release_id "
              "WHERE r.quelle <> 'nr'")
    erste_delta = c.fetchone()[0]                      # OIDs darunter = nr-Stand
    liste = dateien(a.von, a.bis)
    log(f"{len(liste)} Tagesdateien {a.von}..{a.bis}, nr-Stand bis OID {erste_delta - 1}"
        + (" (nur zaehlen)" if a.nur_zaehlen else ""))
    summe = {"acc": 0, "gefunden": 0, "oids": 0, "neu": 0, "acc_der_oids": 0}
    t0 = time.time()
    for datum, name in liste:
        pfad = os.path.join(WORK, name)
        if os.path.exists(pfad + ".done") and not a.nur_zaehlen:
            continue
        if not os.path.exists(pfad):
            urllib.request.urlretrieve(BASE + name, pfad + ".part")
            os.replace(pfad + ".part", pfad)
        eintraege = lesen(pfad)
        beste = {}                                     # oid -> spaetestes LOCUS-Datum
        gef = 0
        for i in range(0, len(eintraege), 1000):
            teil = dict(eintraege[i:i + 1000])
            c.execute("SELECT acc, oid FROM blast_acc WHERE acc IN (" + ",".join(["%s"] * len(teil)) + ")",
                      list(teil))
            for acc, oid in c.fetchall():
                gef += 1
                if oid < erste_delta and teil[acc] > beste.get(oid, ""):
                    beste[oid] = teil[acc]
        neu = 0
        if beste:
            oids = sorted(beste)
            for i in range(0, len(oids), 5000):
                t = oids[i:i + 5000]
                c.execute("SELECT SUM(n_acc) FROM blast_seq WHERE oid IN (" + ",".join(["%s"] * len(t)) + ")", t)
                summe["acc_der_oids"] += int(c.fetchone()[0] or 0)
                if not a.nur_zaehlen:
                    neu += c.executemany("INSERT IGNORE INTO blast_seq_ncbi (oid, ncbi_datum) VALUES (%s, %s)",
                                         [(o, beste[o]) for o in t])
        summe["acc"] += len(eintraege)
        summe["gefunden"] += gef
        summe["oids"] += len(beste)
        summe["neu"] += neu
        if not a.nur_zaehlen:
            open(pfad + ".done", "w").close()
            os.remove(pfad)
        log(f"{name} ({datum}): {len(eintraege)} Accessions, {gef} in blast_acc, {len(beste)} OIDs des nr-Stands, "
            f"{neu} neu eingetragen")
    log(f"Summe nach {time.time() - t0:.0f} s: {summe}")


if __name__ == "__main__":
    main()
