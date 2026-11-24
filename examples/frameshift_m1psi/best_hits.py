#!/usr/bin/env python3
"""Best nr match per +1FS spike peptide, from the raw DIAMOND output.

The exact-match filter answered "is it in nr, letter for letter". This asks the
softer question the user put next: what is the closest thing nr has at all?
Ranked by bitscore, then by covered fraction of the peptide.
"""
import os
import sys

import pymysql

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/fs_all_out"
PARTS = ["p1_mammalia", "p2_eukaryota_other", "p3_viruses_archaea_other",
         "p4_pseudomonadota", "p5_bacillati", "p6_bacteria_other"]

best = {}
for part in PARTS:
    f = os.path.join(OUT, part + ".tsv")
    if not os.path.exists(f):
        continue
    for line in open(f):
        c = line.rstrip("\n").split("\t")
        q, sacc, pid, ln, qlen = c[0], c[1], float(c[2]), int(c[3]), int(c[4])
        bits, ev = float(c[11]), c[10]
        key = (bits, ln / qlen, pid)
        if q not in best or key > best[q][0]:
            best[q] = (key, part, sacc, pid, ln, qlen, ev, bits)

con = pymysql.connect(read_default_file=os.path.expanduser("~/.my.cnf"),
                      read_default_group="wagodb", autocommit=True)
cur = con.cursor()
accs = sorted({v[2] for v in best.values()})
meta = {}
for i in range(0, len(accs), 200):
    ch = accs[i:i + 200]
    cur.execute("SELECT a.acc, s.len, s.n_acc, LEFT(s.title,90), t.name "
                "FROM blast_acc a JOIN blast_seq s ON s.oid=a.oid "
                "LEFT JOIN blast_taxon t ON t.taxid=a.taxid "
                "WHERE a.acc IN (" + ",".join(["%s"] * len(ch)) + ")", ch)
    for acc, ln, nacc, title, org in cur.fetchall():
        meta[acc] = (ln, nacc, title, org)

fs = sorted(q for q in best if q.startswith("fs"))
print(f"+1FS spike peptides with any HSP at all: {len(fs)} of 123\n")
rows = sorted(fs, key=lambda q: -best[q][7])
print(f"{'peptide':8} {'cov':>7} {'pid':>6} {'bits':>6} {'evalue':>10}  subject")
for q in rows:
    _, part, sacc, pid, ln, qlen, ev, bits = best[q]
    m = meta.get(sacc, (0, 0, "", ""))
    print(f"{q.split('_')[0]:8} {ln}/{qlen:<4} {pid:6.1f} {bits:6.1f} {ev:>10}  "
          f"{sacc} {m[3]} | {m[2][:60]}")
