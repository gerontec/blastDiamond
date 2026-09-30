#!/usr/bin/env python3
"""Pre-selection before a DIAMOND search: instead of searching all of nr_full.dmnd (489 GB), the candidate
OIDs are selected in MariaDB, their records are read straight from the .dmnd through its offset table
(dmnd_getseq.Dmnd) and built into a small .dmnd.

Criteria (any combination, all must hold):
  --taxon 9606,4751    taxon including all sub-taxa (blast_taxon.parent), via blast_acc.idx_taxid;
                       a sequence matches if any of its accessions matches (like diamond --taxonlist)
  --len-min/--len-max  length window of the subject sequence (blast_seq.len)
  --new-days 90        NCBI creation date (blast_seq_ncbi.createdate) within the last N days

  dmnd_preselect.py --new-days 90                    builds (or finds in the cache) the sub-.dmnd, prints JSON
  dmnd_preselect.py --taxon 9606 --len-min 100 --len-max 400 --max-seq 5000000

The sub-.dmnd lives in PRESELECT_DIR, keyed by criteria + dmnd_hash (+ date for --new-days), so every nr
append invalidates the cache by itself. Files older than 2 days are removed.
Search it with --dbsize <letters of the full nr> so that E-values stay comparable with a full search.
Records come as makedb stored them (masked) -- exactly what a full search sees.
"""
import argparse, fcntl, hashlib, json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blast_db, pymysql, pymysql.cursors
from dmnd_getseq import Dmnd, luecken as gaps, satznummer as record_no

E = os.environ.get
DMND = E("DMND", "/home/gh/diamond/nr_full.dmnd")
DIAMOND = E("DIAMOND_BIN", os.path.expanduser("~/.local/share/mamba/envs/diamond/bin/diamond"))
PDIR = E("PRESELECT_DIR", "/home/gh/diamond/preselect")
LOCK = E("NR_LOCK", "/home/gh/.nr_dmnd.lock")
MAX_SEQ = int(E("PRESELECT_MAX_SEQ", "50000000"))   # above this a sub-.dmnd no longer pays off
KEEP_S = 2 * 86400


def log(msg):
    print(time.strftime("[%F %T] ") + msg, file=sys.stderr, flush=True)


class TooLarge(Exception):
    pass


def oids(c, taxon=None, len_min=None, len_max=None, new_days=None, max_seq=MAX_SEQ):
    """Sorted OID list for the criteria, driven by the smallest set: creation date, else taxon, else
    length. Raises TooLarge as soon as max_seq is exceeded."""
    if not (taxon or len_min or len_max or new_days):
        raise ValueError("no criterion")
    args, where = [], []
    if taxon:
        c.execute("CREATE TEMPORARY TABLE IF NOT EXISTS ps_tax (taxid INT UNSIGNED PRIMARY KEY) ENGINE=MEMORY")
        c.execute("DELETE FROM ps_tax")
        ph = ",".join(["%s"] * len(taxon))
        c.execute("INSERT IGNORE INTO ps_tax WITH RECURSIVE t AS ("
                  f"SELECT taxid FROM blast_taxon WHERE taxid IN ({ph}) "
                  "UNION SELECT k.taxid FROM blast_taxon k JOIN t ON k.parent = t.taxid AND k.taxid <> k.parent) "
                  "SELECT taxid FROM t", taxon)
        c.execute("SELECT COUNT(*) FROM ps_tax")
        log(f"taxa including sub-taxa: {c.fetchone()[0]}")
    if new_days:
        sql = "SELECT n.oid FROM blast_seq_ncbi n"
        where.append("n.createdate >= CURDATE() - INTERVAL %s DAY")
        args.append(int(new_days))
        if len_min or len_max:
            sql += " JOIN blast_seq s ON s.oid = n.oid"
        if taxon:
            where.append("EXISTS (SELECT 1 FROM blast_acc a JOIN ps_tax t ON t.taxid = a.taxid WHERE a.oid = n.oid)")
    elif taxon:
        sql = "SELECT DISTINCT a.oid FROM ps_tax t JOIN blast_acc a ON a.taxid = t.taxid"
        if len_min or len_max:
            sql += " JOIN blast_seq s ON s.oid = a.oid"
    else:
        sql = "SELECT s.oid FROM blast_seq s"
    if len_min:
        where.append("s.len >= %s")
        args.append(int(len_min))
    if len_max:
        where.append("s.len <= %s")
        args.append(int(len_max))
    if where:
        sql += " WHERE " + " AND ".join(where)
    cs = c.connection.cursor(pymysql.cursors.SSCursor)
    cs.execute(sql, args)
    r = []
    for (o,) in cs:
        r.append(o)
        if len(r) > max_seq:
            cs.close()
            raise TooLarge(f"more than {max_seq} sequences")
    cs.close()
    r.sort()
    return r


def cleanup():
    os.makedirs(PDIR, exist_ok=True)
    for f in os.listdir(PDIR):
        p = os.path.join(PDIR, f)
        if time.time() - os.path.getmtime(p) > KEEP_S:
            os.remove(p)


def preselect(c, taxon=None, len_min=None, len_max=None, new_days=None, max_seq=MAX_SEQ, threads="20"):
    """-> dict(dmnd, n_seq, letters, dbsize, seconds, cache). The caller holds the lock LOCK."""
    t0 = time.time()
    c.execute("SELECT dmnd_hash FROM blast_release WHERE quelle='nr' AND aktiv=1 ORDER BY nr_datum DESC LIMIT 1")
    dhash = c.fetchone()[0]
    crit = {"taxon": sorted(taxon or []), "len_min": len_min, "len_max": len_max, "new_days": new_days,
            "dmnd_hash": dhash, "day": time.strftime("%F") if new_days else None}
    key = hashlib.md5(json.dumps(crit, sort_keys=True).encode()).hexdigest()[:16]
    cleanup()
    target, meta = os.path.join(PDIR, key + ".dmnd"), os.path.join(PDIR, key + ".json")
    if os.path.exists(target) and os.path.exists(meta):
        m = json.load(open(meta))
        os.utime(target), os.utime(meta)
        m.update(cache=True, seconds=round(time.time() - t0, 1))
        return m

    selected = oids(c, taxon, len_min, len_max, new_days, max_seq)
    t1 = time.time()
    log(f"{len(selected)} OIDs from MariaDB in {t1 - t0:.1f} s")
    d = Dmnd(DMND)
    gp = gaps(c)
    fa = os.path.join(PDIR, key + ".fa.tmp")
    letters = 0
    with open(fa, "w") as f:
        for oid in selected:
            name, seq = d.satz(record_no(oid, gp))
            f.write(f">{name.split()[0]}\n{seq}\n")
            letters += len(seq)
    t2 = time.time()
    log(f"FASTA with {letters} letters in {t2 - t1:.1f} s")
    tmp = os.path.join(PDIR, key + ".tmp")
    r = subprocess.run([DIAMOND, "makedb", "--in", fa, "-d", tmp, "--threads", str(threads)],
                       capture_output=True, text=True)
    os.remove(fa)
    if r.returncode != 0:
        raise RuntimeError("makedb: " + (r.stderr or r.stdout)[-1000:])
    os.replace(tmp + ".dmnd", target)
    log(f"makedb in {time.time() - t2:.1f} s")
    m = {"dmnd": target, "n_seq": len(selected), "letters": letters, "dbsize": d.letters, "criteria": crit}
    json.dump(m, open(meta, "w"))
    m.update(cache=False, seconds=round(time.time() - t0, 1))
    return m


def main():
    p = argparse.ArgumentParser(description="Build a sub-.dmnd for a DIAMOND search from MariaDB criteria")
    p.add_argument("--taxon", default="", help="TaxIDs, comma-separated, including sub-taxa")
    p.add_argument("--len-min", type=int)
    p.add_argument("--len-max", type=int)
    p.add_argument("--new-days", type=int, help="NCBI creation date within the last N days")
    p.add_argument("--max-seq", type=int, default=MAX_SEQ)
    a = p.parse_args()
    taxon = [int(x) for x in a.taxon.split(",") if x.strip()]
    c = pymysql.connect(**blast_db.cfg(), autocommit=True).cursor()
    with open(LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        try:
            m = preselect(c, taxon, a.len_min, a.len_max, a.new_days, a.max_seq)
        except TooLarge as e:
            print(json.dumps({"too_large": str(e)}))
            return 2
    print(json.dumps(m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
