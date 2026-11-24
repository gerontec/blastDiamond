#!/usr/bin/env python3
"""Search the published +1 frameshift peptides against the six nr part DBs.

Background
----------
Mulroney et al., Nature 625, 189-194 (2024), "N1-methylpseudouridylation of
mRNA causes +1 ribosomal frameshifting", report that m1-pseudouridine makes the
ribosome slip by one base at runs of that base, so a modified mRNA such as
BNT162b2 produces, besides the intended antigen, chimeric polypeptides that are
in frame at the N terminus and out of frame behind the slip.

None of those products is a deposited sequence, so this run asks the opposite
question: does any of the published peptides occur, over its whole length and
at 100 % identity, anywhere in NCBI nr -- and if so, in which part DB?

Three query classes, distinguished by the FASTA id prefix:

  fs...  123 +1FS spike peptides, Extended Data Table 3 (predicted from the
         BNT162b2 +1 frame; the pool used for the human ELISpot)
  ms...    9 +1FS peptides of the Fluc reporter, Extended Data Table 1
         (identified by LC-MS/MS, so these were physically present)
  ct...    6 in-frame Fluc peptides, Extended Data Table 1. Positive control:
         firefly luciferase is in nr (P08659), so these MUST be found. If they
         are not, the search is misconfigured and a negative result means
         nothing.

Companion to examples/frameshift_ubb1.md, which asks the same question for the
computed products of molecular misreading and uses the same part DBs.

Usage
-----
  fs_peptide_search.py <query.fa> <out_dir> [--mode fast|ultra] [part ...]

Writes <part>.tsv (raw DIAMOND output), runtimes.tsv, hits.tsv (the alignments
that cover a whole query at 100 % identity) and, for those, meta.tsv joined
against wagodb (blast_acc, blast_seq, blast_taxon).
"""
import os
import subprocess
import sys
import time

DIAMOND = os.path.expanduser("~/.local/share/mamba/envs/diamond/bin/diamond")
PARTS_DIR = os.path.expanduser("~/diamond/parts")
PARTS = ["p1_mammalia", "p2_eukaryota_other", "p3_viruses_archaea_other",
         "p4_pseudomonadota", "p5_bacillati", "p6_bacteria_other"]

FIELDS = ("qseqid sseqid pident length qlen slen qstart qend sstart send "
          "evalue bitscore staxids").split()

# "fast" follows the convention of pipeline/frameshift_scan.py, so that the
# result is comparable with the UBB+1 run documented in examples/.
# "ultra" is the sensitivity control; it costs roughly 25x the wall clock.
MODES = {
    "fast": ["--evalue", "1000", "--max-target-seqs", "50"],
    "ultra": ["--ultra-sensitive", "--masking", "0", "--comp-based-stats", "0",
              "--evalue", "10000", "--max-target-seqs", "50"],
}
COMMON = ["--threads", "20", "--block-size", "2", "--index-chunks", "1"]


def nr_letters(cur):
    """Total letter count over all parts, so E-values match a whole-nr search."""
    cur.execute("SELECT SUM(letters) FROM blast_part")
    return int(cur.fetchone()[0])


def db_cursor():
    import pymysql
    con = pymysql.connect(read_default_file=os.path.expanduser("~/.my.cnf"),
                          read_default_group="wagodb", autocommit=True)
    return con.cursor()


def run_part(part, query, out_dir, mode, dbsize):
    db = os.path.join(PARTS_DIR, part + ".dmnd")
    out = os.path.join(out_dir, part + ".tsv")
    cmd = ([DIAMOND, "blastp", "-d", db, "-q", query, "-o", out, "--outfmt", "6"]
           + FIELDS + MODES[mode] + COMMON + ["--dbsize", str(dbsize)])
    t0 = time.monotonic()
    p = subprocess.run(cmd, capture_output=True, text=True)
    dt = time.monotonic() - t0
    open(os.path.join(out_dir, part + ".log"), "w").write(p.stderr)
    n = sum(1 for _ in open(out)) if os.path.exists(out) else 0
    print(f"{part}\t{dt:.1f}s\trc={p.returncode}\trows={n}", flush=True)
    return {"part": part, "seconds": round(dt, 1), "rc": p.returncode, "rows": n}


def exact_hits(out_dir, parts):
    """Alignments covering the whole query at 100 % identity."""
    rows = []
    for part in parts:
        f = os.path.join(out_dir, part + ".tsv")
        if not os.path.exists(f):
            continue
        for line in open(f):
            c = line.rstrip("\n").split("\t")
            if float(c[2]) == 100.0 and int(c[3]) == int(c[4]):
                rows.append([part] + c)
    return rows


def join_metadata(cur, hits):
    """Attach the wagodb metadata of each hit subject."""
    accs = sorted({h[2].split("|")[-1] if "|" in h[2] else h[2] for h in hits})
    meta = {}
    for i in range(0, len(accs), 200):
        chunk = accs[i:i + 200]
        ph = ",".join(["%s"] * len(chunk))
        cur.execute(
            "SELECT a.acc, a.taxid, s.len, s.title, t.name, t.rank "
            "FROM blast_acc a JOIN blast_seq s ON s.oid = a.oid "
            "LEFT JOIN blast_taxon t ON t.taxid = a.taxid "
            f"WHERE a.acc IN ({ph})", chunk)
        for acc, taxid, ln, title, name, rank in cur.fetchall():
            meta[acc] = (taxid, ln, (title or "")[:200], name or "", rank or "")
    return meta


def main():
    args = [a for a in sys.argv[1:]]
    mode = "fast"
    if "--mode" in args:
        i = args.index("--mode")
        mode = args[i + 1]
        del args[i:i + 2]
    query, out_dir = args[0], args[1]
    parts = args[2:] or PARTS
    os.makedirs(out_dir, exist_ok=True)

    cur = db_cursor()
    dbsize = nr_letters(cur)
    print(f"mode={mode} dbsize={dbsize} parts={len(parts)}", flush=True)
    open(os.path.join(out_dir, "dbsize.txt"), "w").write(str(dbsize))

    times = [run_part(p, query, out_dir, mode, dbsize) for p in parts]

    with open(os.path.join(out_dir, "runtimes.tsv"), "w") as fh:
        fh.write("part\tseconds\trc\trows\tmode\n")
        for t in times:
            fh.write(f"{t['part']}\t{t['seconds']}\t{t['rc']}\t{t['rows']}\t{mode}\n")
        fh.write(f"TOTAL\t{round(sum(t['seconds'] for t in times), 1)}\t\t\t{mode}\n")

    hits = exact_hits(out_dir, parts)
    with open(os.path.join(out_dir, "hits.tsv"), "w") as fh:
        fh.write("part\t" + "\t".join(FIELDS) + "\n")
        for h in hits:
            fh.write("\t".join(h) + "\n")

    meta = join_metadata(cur, hits) if hits else {}
    with open(os.path.join(out_dir, "meta.tsv"), "w") as fh:
        fh.write("part\tquery\tacc\ttaxid\tseq_len\torganism\trank\ttitle\n")
        for h in hits:
            acc = h[2].split("|")[-1] if "|" in h[2] else h[2]
            m = meta.get(acc, ("", "", "", "", ""))
            fh.write("\t".join(str(x) for x in
                               [h[0], h[1], acc, m[0], m[1], m[3], m[4], m[2]]) + "\n")

    print(f"exact full-length hits: {len(hits)}  (with metadata: {len(meta)})")


if __name__ == "__main__":
    main()
