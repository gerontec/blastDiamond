#!/usr/bin/env python3
"""Vorauswahl vor der DIAMOND-Suche: statt die ganze nr_full.dmnd (489 GB) zu durchsuchen,
werden die in Frage kommenden OIDs per MariaDB bestimmt, ihre Saetze direkt ueber die
Offset-Tabelle aus der .dmnd geholt (dmnd_getseq.Dmnd) und zu einer kleinen .dmnd gebaut.

Kriterien (beliebig kombinierbar, alle muessen zutreffen):
  --taxon 9606,4751   Taxon samt allen Untertaxa (blast_taxon.parent), ueber blast_acc.idx_taxid;
                      eine Sequenz zaehlt, wenn irgendeine ihrer Accessions passt (wie diamond --taxonlist)
  --len-min/--len-max Laengenfenster der Subjekt-Sequenz (blast_seq.idx_len)
  --neu-tage 90       NCBI-Anlagedatum (blast_seq_ncbi.createdate) in den letzten N Tagen

  dmnd_vorauswahl.py --neu-tage 90                 baut (oder findet im Cache) die Teil-.dmnd, gibt JSON aus
  dmnd_vorauswahl.py --taxon 9606 --len-min 100 --len-max 400 --max-seq 5000000

Die Teil-.dmnd liegt in VORAUSWAHL_DIR, Schluessel = Kriterien + dmnd_hash (+ Datum bei --neu-tage);
nach jedem nr-Append ist der Cache damit automatisch veraltet. Aufraeumen: aelter als 2 Tage.
Suche dagegen mit --dbsize <Residuen der vollen nr>, damit die E-Werte mit der Vollsuche vergleichbar bleiben.
Saetze kommen so, wie makedb sie gespeichert hat (maskiert) -- dasselbe, was die Vollsuche sieht.
"""
import argparse, fcntl, hashlib, json, os, subprocess, sys, time

sys.path.insert(0, "/home/gh/python")
import blast_db, pymysql, pymysql.cursors
from dmnd_getseq import Dmnd, luecken, satznummer

E = os.environ.get
DMND = E("DMND", "/home/gh/diamond/nr_full.dmnd")
DIAMOND = E("DIAMOND_BIN", os.path.expanduser("~/.local/share/mamba/envs/diamond/bin/diamond"))
VDIR = E("VORAUSWAHL_DIR", "/home/gh/diamond/vorauswahl")
LOCK = E("NR_LOCK", "/home/gh/.nr_dmnd.lock")
MAX_SEQ = int(E("VORAUSWAHL_MAX_SEQ", "50000000"))   # darueber lohnt die Teil-.dmnd nicht mehr
HALTEN_S = 2 * 86400


def log(msg):
    print(time.strftime("[%F %T] ") + msg, file=sys.stderr, flush=True)


class ZuGross(Exception):
    pass


def oids(c, taxon=None, len_min=None, len_max=None, neu_tage=None, max_seq=MAX_SEQ):
    """Sortierte OID-Liste zu den Kriterien. Getrieben von der kleinsten Menge:
    Anlagedatum, sonst Taxon, sonst Laenge. ZuGross, sobald max_seq ueberschritten wird."""
    if not (taxon or len_min or len_max or neu_tage):
        raise ValueError("kein Kriterium")
    args, wo = [], []
    if taxon:
        c.execute("CREATE TEMPORARY TABLE IF NOT EXISTS vw_tax (taxid INT UNSIGNED PRIMARY KEY) ENGINE=MEMORY")
        c.execute("DELETE FROM vw_tax")
        ph = ",".join(["%s"] * len(taxon))
        c.execute("INSERT IGNORE INTO vw_tax WITH RECURSIVE t AS ("
                  f"SELECT taxid FROM blast_taxon WHERE taxid IN ({ph}) "
                  "UNION SELECT k.taxid FROM blast_taxon k JOIN t ON k.parent = t.taxid AND k.taxid <> k.parent) "
                  "SELECT taxid FROM t", taxon)
        c.execute("SELECT COUNT(*) FROM vw_tax")
        log(f"Taxa samt Untertaxa: {c.fetchone()[0]}")
    if neu_tage:
        sql = "SELECT n.oid FROM blast_seq_ncbi n"
        wo.append("n.createdate >= CURDATE() - INTERVAL %s DAY")
        args.append(int(neu_tage))
        if len_min or len_max:
            sql += " JOIN blast_seq s ON s.oid = n.oid"
        if taxon:
            wo.append("EXISTS (SELECT 1 FROM blast_acc a JOIN vw_tax t ON t.taxid = a.taxid WHERE a.oid = n.oid)")
    elif taxon:
        sql = "SELECT DISTINCT a.oid FROM vw_tax t JOIN blast_acc a ON a.taxid = t.taxid"
        if len_min or len_max:
            sql += " JOIN blast_seq s ON s.oid = a.oid"
    else:
        sql = "SELECT s.oid FROM blast_seq s"
    if len_min:
        wo.append("s.len >= %s")
        args.append(int(len_min))
    if len_max:
        wo.append("s.len <= %s")
        args.append(int(len_max))
    if wo:
        sql += " WHERE " + " AND ".join(wo)
    cs = c.connection.cursor(pymysql.cursors.SSCursor)
    cs.execute(sql, args)
    r = []
    for (o,) in cs:
        r.append(o)
        if len(r) > max_seq:
            cs.close()
            raise ZuGross(f"mehr als {max_seq} Sequenzen")
    cs.close()
    r.sort()
    return r


def aufraeumen():
    os.makedirs(VDIR, exist_ok=True)
    for f in os.listdir(VDIR):
        p = os.path.join(VDIR, f)
        if time.time() - os.path.getmtime(p) > HALTEN_S:
            os.remove(p)


def vorauswahl(c, taxon=None, len_min=None, len_max=None, neu_tage=None, max_seq=MAX_SEQ, threads="20"):
    """-> dict(dmnd, n_seq, letters, dbsize, sekunden, cache). Der Aufrufer haelt die Sperre LOCK."""
    t0 = time.time()
    c.execute("SELECT dmnd_hash FROM blast_release WHERE quelle='nr' AND aktiv=1 ORDER BY nr_datum DESC LIMIT 1")
    dhash = c.fetchone()[0]
    krit = {"taxon": sorted(taxon or []), "len_min": len_min, "len_max": len_max, "neu_tage": neu_tage,
            "dmnd_hash": dhash, "tag": time.strftime("%F") if neu_tage else None}
    key = hashlib.md5(json.dumps(krit, sort_keys=True).encode()).hexdigest()[:16]
    aufraeumen()
    ziel, meta = os.path.join(VDIR, key + ".dmnd"), os.path.join(VDIR, key + ".json")
    if os.path.exists(ziel) and os.path.exists(meta):
        m = json.load(open(meta))
        os.utime(ziel), os.utime(meta)
        m.update(cache=True, sekunden=round(time.time() - t0, 1))
        return m

    liste = oids(c, taxon, len_min, len_max, neu_tage, max_seq)
    t1 = time.time()
    log(f"{len(liste)} OIDs aus MariaDB in {t1 - t0:.1f} s")
    d = Dmnd(DMND)
    lk = luecken(c)
    fa = os.path.join(VDIR, key + ".fa.tmp")
    letters = 0
    with open(fa, "w") as f:
        for oid in liste:
            name, seq = d.satz(satznummer(oid, lk))
            f.write(f">{name.split()[0]}\n{seq}\n")
            letters += len(seq)
    t2 = time.time()
    log(f"FASTA {letters} Residuen in {t2 - t1:.1f} s")
    tmp = os.path.join(VDIR, key + ".tmp")
    r = subprocess.run([DIAMOND, "makedb", "--in", fa, "-d", tmp, "--threads", str(threads)],
                       capture_output=True, text=True)
    os.remove(fa)
    if r.returncode != 0:
        raise RuntimeError("makedb: " + (r.stderr or r.stdout)[-1000:])
    os.replace(tmp + ".dmnd", ziel)
    log(f"makedb in {time.time() - t2:.1f} s")
    m = {"dmnd": ziel, "n_seq": len(liste), "letters": letters, "dbsize": d.letters, "kriterien": krit}
    json.dump(m, open(meta, "w"))
    m.update(cache=False, sekunden=round(time.time() - t0, 1))
    return m


def main():
    p = argparse.ArgumentParser(description="Teil-.dmnd fuer die DIAMOND-Suche aus MariaDB-Kriterien bauen")
    p.add_argument("--taxon", default="", help="TaxIDs, kommagetrennt, samt Untertaxa")
    p.add_argument("--len-min", type=int)
    p.add_argument("--len-max", type=int)
    p.add_argument("--neu-tage", type=int, help="NCBI-Anlagedatum in den letzten N Tagen")
    p.add_argument("--max-seq", type=int, default=MAX_SEQ)
    a = p.parse_args()
    taxon = [int(x) for x in a.taxon.split(",") if x.strip()]
    c = pymysql.connect(**blast_db.cfg(), autocommit=True).cursor()
    with open(LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        try:
            m = vorauswahl(c, taxon, a.len_min, a.len_max, a.neu_tage, a.max_seq)
        except ZuGross as e:
            print(json.dumps({"zu_gross": str(e)}))
            return 2
    print(json.dumps(m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
