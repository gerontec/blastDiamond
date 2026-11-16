#!/usr/bin/env python3
"""Cluster the sequences each daily nr update added, among themselves, into a two-level tree (doc/nr_cluster.md).

An update is identified by its OID end (blast_release.dmnd_sequenzen after nr_daily_delta.py); its new OIDs are
[previous end, end). Per part, the new records are the tail of <part>.oids.npy; they are read from the part's
.dmnd under a shared lock on the nr lock file, written as FASTA with the OID as header, and clustered with MMseqs2:
90 % identity over all new records, then 50 % over the 90 % representatives (member coverage 80 %, --cov-mode 1,
at most 20 prefilter candidates per query). Only the new sequences are compared with each other, not with the
rest of nr.

Tables (schema wagodb):
  blast_cluster      (oid, part, rep90_oid, rep50_oid, delta_end): one row per new sequence; a representative points
                     to itself
  blast_cluster_rep  (rep_oid, part, level, n_members, delta_end): one row per representative and level (90 / 50)
  blast_cluster_run  (delta_end, part, n_seq, n_rep90, n_rep50, seconds, finished): one row per update and part

The first run processes only the newest update; later runs every update after the last processed one, so a missed
day is caught up. Credentials from ~/.my.cnf, section [wagodb].

  python3 nr_cluster_daily.py                 cron, after nr_daily_delta.py
  python3 nr_cluster_daily.py --dry-run       show which updates and parts would be clustered
"""
import argparse
import fcntl
import os
import shutil
import subprocess
import sys
import time

import numpy as np
import pymysql

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nr_parts import NrParts

PARTS = ("p1_mammalia", "p2_eukaryota_other", "p3_viruses_archaea_other", "p4_pseudomonadota", "p5_bacillati",
         "p6_bacteria_other")
MMSEQS = os.environ.get("MMSEQS", os.path.expanduser("~/src/MMseqs2/build/src/mmseqs"))
LOCK = os.environ.get("NR_LOCK", os.path.expanduser("~/.nr_dmnd.lock"))
WORK = os.environ.get("NR_CLUSTER_WORK", "/mnt/archive/nr_cluster_daily")
PARAMS = ["-c", "0.8", "--cov-mode", "1", "--max-seqs", "20"]
ENUM = ",".join(repr(p) for p in PARTS)
DDL = [
    f"""CREATE TABLE IF NOT EXISTS blast_cluster (
        oid INT UNSIGNED NOT NULL,
        part ENUM({ENUM}) NOT NULL,
        rep90_oid INT UNSIGNED NOT NULL COMMENT '90 % identity representative among the same update (MMseqs2)',
        rep50_oid INT UNSIGNED NOT NULL COMMENT '50 % representative, clustered over the 90 % representatives',
        delta_end INT UNSIGNED NOT NULL COMMENT 'OID end of the daily update that added the sequence',
        PRIMARY KEY (oid, part),
        KEY (rep90_oid),
        KEY (rep50_oid),
        KEY (delta_end)
    ) COMMENT 'clusters of the sequences of each daily nr update, among themselves (nr_cluster_daily.py)'""",
    f"""CREATE TABLE IF NOT EXISTS blast_cluster_rep (
        rep_oid INT UNSIGNED NOT NULL,
        part ENUM({ENUM}) NOT NULL,
        level TINYINT UNSIGNED NOT NULL COMMENT '90 or 50',
        n_members INT UNSIGNED NOT NULL COMMENT 'sequences in the cluster, the representative included',
        delta_end INT UNSIGNED NOT NULL,
        PRIMARY KEY (rep_oid, part, level),
        KEY (delta_end, part, level, n_members)
    ) COMMENT 'representatives of blast_cluster with their sizes (nr_cluster_daily.py)'""",
    f"""CREATE TABLE IF NOT EXISTS blast_cluster_run (
        delta_end INT UNSIGNED NOT NULL,
        part ENUM({ENUM}) NOT NULL,
        n_seq INT UNSIGNED NOT NULL,
        n_rep90 INT UNSIGNED NOT NULL,
        n_rep50 INT UNSIGNED NOT NULL,
        seconds INT UNSIGNED NOT NULL,
        mmseqs_version VARCHAR(64),
        finished DATETIME NOT NULL,
        PRIMARY KEY (delta_end, part)
    ) COMMENT 'one row per daily update and part clustered by nr_cluster_daily.py'""",
]


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def connect():
    return pymysql.connect(read_default_file=os.path.expanduser("~/.my.cnf"), read_default_group="wagodb",
                           database="wagodb", local_infile=True, connect_timeout=10, read_timeout=3600)


def updates_to_do(c):
    """[(start, end)] of the updates not yet clustered; the first run takes only the newest one."""
    c.execute("""SELECT DISTINCT dmnd_sequenzen FROM blast_release
                 WHERE dmnd_sequenzen IS NOT NULL AND dmnd_datei = 'nr parts' ORDER BY dmnd_sequenzen""")
    ends = [int(r[0]) for r in c.fetchall()]
    pairs = list(zip(ends, ends[1:]))
    c.execute("SELECT MAX(delta_end) FROM blast_cluster_run")
    last = c.fetchone()[0]
    if last is None:
        return pairs[-1:]
    return [(s, e) for s, e in pairs if e > int(last)]


def mm(args, logfile):
    with open(logfile, "a") as f:
        subprocess.run([MMSEQS] + args, check=True, stdout=f, stderr=subprocess.STDOUT)


def cluster_part(c, nr, part, start, end, threads):
    """Cluster the new records of one part; returns (n_seq, n_rep90, n_rep50) and writes the rows."""
    oids = np.asarray(nr.oids(part))
    lo, hi = np.searchsorted(oids, [start, end])
    new = oids[lo:hi]
    if len(new) == 0:
        return 0, 0, 0
    d = os.path.join(WORK, str(end), part)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    logfile = os.path.join(d, "mmseqs.log")
    fasta = os.path.join(d, "new.faa")
    db = nr.dmnd(part)
    with open(fasta, "w") as f:
        for rank, oid in zip(range(lo, hi), new.tolist()):
            _, seq = db.satz(rank)
            f.write(f">{oid}\n{seq}\n")
    rep90 = {int(o): int(o) for o in new}
    rep50 = dict(rep90)
    if len(new) > 1:
        p = PARAMS + ["--threads", str(threads), "-v", "1"]
        mm(["createdb", fasta, f"{d}/seq", "-v", "1"], logfile)
        mm(["cluster", f"{d}/seq", f"{d}/clu90", f"{d}/tmp90", "--min-seq-id", "0.9"] + p, logfile)
        mm(["createsubdb", f"{d}/clu90", f"{d}/seq", f"{d}/rep90", "-v", "1"], logfile)
        mm(["cluster", f"{d}/rep90", f"{d}/clu50", f"{d}/tmp50", "--min-seq-id", "0.5"] + p, logfile)
        mm(["createtsv", f"{d}/seq", f"{d}/seq", f"{d}/clu90", f"{d}/clu90.tsv", "-v", "1"], logfile)
        mm(["createtsv", f"{d}/rep90", f"{d}/rep90", f"{d}/clu50", f"{d}/clu50.tsv", "-v", "1"], logfile)
        rep90 = {int(m): int(r) for r, m in (l.split("\t")[:2] for l in open(f"{d}/clu90.tsv"))}
        r50 = {int(m): int(r) for r, m in (l.split("\t")[:2] for l in open(f"{d}/clu50.tsv"))}
        if len(rep90) != len(new) or set(r50) != set(rep90.values()):
            raise SystemExit(f"{part} {end}: incomplete clustering ({len(rep90)}/{len(new)} members, "
                             f"{len(r50)} 50 % members for {len(set(rep90.values()))} 90 % representatives)")
        rep50 = {o: r50[r] for o, r in rep90.items()}
    rows = [(o, part, rep90[o], rep50[o], end) for o in sorted(rep90)]
    c.executemany("REPLACE INTO blast_cluster (oid, part, rep90_oid, rep50_oid, delta_end) VALUES (%s,%s,%s,%s,%s)",
                  rows)
    for level, reps in ((90, rep90), (50, rep50)):
        counts = {}
        for r in reps.values():
            counts[r] = counts.get(r, 0) + 1
        c.executemany("REPLACE INTO blast_cluster_rep (rep_oid, part, level, n_members, delta_end) "
                      "VALUES (%s,%s,%s,%s,%s)", [(r, part, level, n, end) for r, n in counts.items()])
    shutil.rmtree(d, ignore_errors=True)
    return len(new), len(set(rep90.values())), len(set(rep50.values()))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    con = connect()
    c = con.cursor()
    for ddl in DDL:
        c.execute(ddl)
    todo = updates_to_do(c)
    if not todo:
        log("nothing to do")
        return
    version = subprocess.run([MMSEQS, "version"], capture_output=True, text=True).stdout.strip()[:64]
    with open(LOCK, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_SH)          # the daily delta appends under LOCK_EX
        nr = NrParts(c)
        for start, end in todo:
            log(f"update {start}..{end - 1}: {end - start} new OIDs")
            for part in PARTS:
                if a.dry_run:
                    oids = np.asarray(nr.oids(part))
                    lo, hi = np.searchsorted(oids, [start, end])
                    log(f"  {part}: {hi - lo} new records")
                    continue
                t0 = time.time()
                n, n90, n50 = cluster_part(c, nr, part, start, end, a.threads)
                c.execute("REPLACE INTO blast_cluster_run (delta_end, part, n_seq, n_rep90, n_rep50, seconds, "
                          "mmseqs_version, finished) VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())",
                          (end, part, n, n90, n50, int(time.time() - t0), version))
                con.commit()
                log(f"  {part}: {n} sequences -> {n90} clusters at 90 %, {n50} at 50 % ({time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main()
