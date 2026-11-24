#!/usr/bin/env python3
"""Cluster the published +1 frameshift peptides together with the human part of nr.

This deliberately breaks the standing rule of nr_cluster_daily.py ("new sequences
are compared only with each other, not with the rest of nr"): the peptides are in
no nr release at all, so the daily run would never touch them. Restricting the
counterpart to Homo sapiens (taxid 9606, about 2.29 M sequences) keeps the run
affordable while answering the question that matters -- does any of these
products group with a human protein?

Two levels as in the daily run, 90 % then 50 % identity on the 90 %
representatives. One parameter differs and has to: the daily run uses
--cov-mode 1 (coverage of the target), under which a 15 aa peptide can never
join a 550 aa protein, because 80 % of the protein would have to be covered.
Here --cov-mode 2 (coverage of the shorter sequence) is used, so a peptide can
become a member of a longer representative's cluster. With --cov-mode 1 every
peptide would be a singleton by construction, which would prove nothing.

Nothing is written to blast_cluster / blast_cluster_rep: those tables are keyed
by (oid, part) and carry the semantics of a daily update. This is an analysis,
its result goes to TSV.

Usage: fs_peptide_cluster.py <peptides.fa> <out_dir> [--taxid 9606] [--threads 20]
"""
import fcntl
import os
import subprocess
import sys
import time

import numpy as np
import pymysql

PIPE = os.path.expanduser("~/src/blastDiamond/pipeline")
sys.path.insert(0, PIPE)
from nr_parts import NrParts  # noqa: E402

MMSEQS = os.environ.get("MMSEQS", os.path.expanduser("~/src/MMseqs2/build/src/mmseqs"))
LOCK = os.path.expanduser("~/.nr_dmnd.lock")
PARAMS = ["-c", "0.8", "--cov-mode", "2", "--max-seqs", "20"]


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def mm(args, logfile):
    with open(logfile, "a") as fh:
        p = subprocess.run([MMSEQS] + args, stdout=fh, stderr=subprocess.STDOUT)
    if p.returncode:
        sys.exit(f"mmseqs {args[0]} failed, see {logfile}")


def read_fa(path):
    out, name = [], None
    for line in open(path):
        line = line.strip()
        if line.startswith(">"):
            name = line[1:].split()[0]
            out.append([name, ""])
        elif name:
            out[-1][1] += line
    return out


def main():
    pep_fa, out_dir = sys.argv[1], sys.argv[2]
    a = sys.argv[3:]
    taxid = int(a[a.index("--taxid") + 1]) if "--taxid" in a else 9606
    threads = a[a.index("--threads") + 1] if "--threads" in a else "20"
    os.makedirs(out_dir, exist_ok=True)
    logfile = os.path.join(out_dir, "mmseqs.log")

    con = pymysql.connect(read_default_file=os.path.expanduser("~/.my.cnf"),
                          read_default_group="wagodb", autocommit=True)
    cur = con.cursor()
    cur.execute("SET SESSION max_statement_time=600")
    log(f"selecting OIDs of taxid {taxid} ...")
    cur.execute("SELECT DISTINCT oid FROM blast_acc WHERE taxid=%s", (taxid,))
    oids = np.array(sorted(r[0] for r in cur.fetchall()), dtype=np.uint64)
    log(f"{len(oids)} sequences")

    fasta = os.path.join(out_dir, "input.faa")
    lock = open(LOCK, "a+")
    fcntl.flock(lock, fcntl.LOCK_SH)
    try:
        nr = NrParts(cur)
        t0 = time.monotonic()
        n = 0
        with open(fasta, "w") as fh:
            for oid, _name, seq in nr.records(oids):
                fh.write(f">nr_{oid}\n{seq}\n")
                n += 1
            for name, seq in read_fa(pep_fa):
                fh.write(f">pep_{name}\n{seq}\n")
        log(f"wrote {n} nr sequences + peptides in {time.monotonic()-t0:.0f}s")
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)

    d = out_dir
    p = PARAMS + ["--threads", threads, "-v", "1"]
    t0 = time.monotonic()
    mm(["createdb", fasta, f"{d}/seq", "-v", "1"], logfile)
    mm(["cluster", f"{d}/seq", f"{d}/clu90", f"{d}/tmp90", "--min-seq-id", "0.9"] + p, logfile)
    mm(["createsubdb", f"{d}/clu90", f"{d}/seq", f"{d}/rep90", "-v", "1"], logfile)
    mm(["cluster", f"{d}/rep90", f"{d}/clu50", f"{d}/tmp50", "--min-seq-id", "0.5"] + p, logfile)
    mm(["createtsv", f"{d}/seq", f"{d}/seq", f"{d}/clu90", f"{d}/clu90.tsv", "-v", "1"], logfile)
    mm(["createtsv", f"{d}/rep90", f"{d}/rep90", f"{d}/clu50", f"{d}/clu50.tsv", "-v", "1"], logfile)
    secs = time.monotonic() - t0
    log(f"mmseqs done in {secs:.0f}s")

    rep90 = {}
    for line in open(f"{d}/clu90.tsv"):
        r, m = line.rstrip("\n").split("\t")[:2]
        rep90[m] = r
    rep50 = {}
    for line in open(f"{d}/clu50.tsv"):
        r, m = line.rstrip("\n").split("\t")[:2]
        rep50[m] = r

    size90, size50 = {}, {}
    for m, r in rep90.items():
        size90[r] = size90.get(r, 0) + 1
    for m, r in rep90.items():
        r50 = rep50.get(r, r)
        size50[r50] = size50.get(r50, 0) + 1

    peps = [m for m in rep90 if m.startswith("pep_")]
    grouped = []
    for m in sorted(peps):
        r90 = rep90[m]
        r50 = rep50.get(r90, r90)
        grouped.append((m, r90, size90.get(r90, 1), r50, size50.get(r50, 1)))

    with open(os.path.join(out_dir, "peptide_clusters.tsv"), "w") as fh:
        fh.write("peptide\trep90\tn_members90\trep50\tn_members50\tsingleton\n")
        for m, r90, n90, r50, n50 in grouped:
            single = "yes" if (n90 == 1 and n50 == 1) else "no"
            fh.write(f"{m}\t{r90}\t{n90}\t{r50}\t{n50}\t{single}\n")

    with open(os.path.join(out_dir, "cluster_runtime.tsv"), "w") as fh:
        fh.write("step\tseconds\tn_nr\tn_pep\ttaxid\tcov_mode\n")
        fh.write(f"mmseqs_two_level\t{secs:.1f}\t{n}\t{len(peps)}\t{taxid}\t2\n")

    nonsingle = [g for g in grouped if not (g[2] == 1 and g[4] == 1)]
    log(f"peptides: {len(peps)}, not a singleton: {len(nonsingle)}")
    for g in nonsingle[:40]:
        log(f"  {g[0]}  rep90={g[1]} (n={g[2]})  rep50={g[3]} (n={g[4]})")


if __name__ == "__main__":
    main()
