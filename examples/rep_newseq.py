#!/usr/bin/env python3
"""Report: new spike sequences in NCBI nr -- SARS-CoV-2 spike S1/S2 against all proteins created
within the last N days (default 90), with the runtime of every step.

Steps (each timed):
  1. pre-selection  dmnd_preselect.preselect(new_days=N): OIDs from blast_seq_ncbi.createdate,
                    records read from nr_full.dmnd, small sub-.dmnd built (cached per day + dmnd_hash)
  2. search         diamond blastp S1 + S2, --very-sensitive, all hits, --dbsize of the full nr
                    so E-values match a search against all of nr
  3. metadata       accession -> OID, length, title, taxid, organism, createdate from MariaDB

Measured on dell 2026-10-01 (20 threads), 90 days = 2,543,666 sequences / 1.08 G letters:
  pre-selection     27.7 s cold (MariaDB 3.5 s, records from the .dmnd 20.2 s, makedb 3.9 s), 0.0 s cached
  diamond blastp    13.1 s --very-sensitive (16 shapes, peak RSS 2.8 GB)
                    41-46 s --ultra-sensitive (64 shapes, 1.9 GB) -- byte-identical hits for this query
                    --index-chunks 1 changes nothing: DIAMOND picks the query-indexed algorithm here
  metadata          < 0.1 s
  found             1,016 hits (S1 506, S2 510), 510 distinct sequences, 4 of them not SARS-CoV-2

Output: CSV (header lines with query, database, parameters and runtimes) and a summary on stdout.
Limitation: creation dates exist only for sequences that came in through the daily deltas (since
2026-09-16), so "last N days" means at most the time since then plus what the backfill dated.

  python3 ~/rep_newseq.py                     # 90 days, S1/S2 of YP_009724390.1
  python3 ~/rep_newseq.py --days 30 --out /tmp/x.csv
"""
import argparse, csv, fcntl, os, subprocess, sys, tempfile, time
from collections import Counter

sys.path.insert(0, os.path.expanduser("~/python"))
import blast_db, pymysql
import dmnd_preselect

E = os.environ.get
DIAMOND = E("DIAMOND_BIN", os.path.expanduser("~/.local/share/mamba/envs/diamond/bin/diamond"))
LOCK = E("NR_LOCK", "/home/gh/.nr_dmnd.lock")
TMP = E("JOB_TMP", "/mnt/archive/blast_api_tmp")
QUERY = os.path.expanduser("~/src/blastDiamond/examples/sars2_spike_s1s2_query.fa")
FIELDS = "qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore".split()
SARS2 = 2697049


def log(msg):
    print(time.strftime("[%F %T] ") + msg, file=sys.stderr, flush=True)


def metadata(c, accs):
    """acc -> (oid, len, title, taxid, organism, createdate, ncbi_date); taxid of the hit accession"""
    out = {}
    accs = sorted(accs)
    for i in range(0, len(accs), 500):
        part = accs[i:i + 500]
        c.execute("SELECT a.acc, a.oid, s.len, s.title, a.taxid, t.name, n.createdate, n.ncbi_datum "
                  "FROM blast_acc a JOIN blast_seq s ON s.oid = a.oid "
                  "LEFT JOIN blast_taxon t ON t.taxid = a.taxid "
                  "LEFT JOIN blast_seq_ncbi n ON n.oid = a.oid "
                  "WHERE a.acc IN (" + ",".join(["%s"] * len(part)) + ")", part)
        for r in c.fetchall():
            out[r[0]] = r[1:]
    return out


def main():
    p = argparse.ArgumentParser(description="SARS-CoV-2 spike S1/S2 vs. recently created nr proteins")
    p.add_argument("--days", type=int, default=90, help="NCBI creation date within the last N days")
    p.add_argument("--query", default=QUERY, help="FASTA, default S1/S2 of YP_009724390.1")
    p.add_argument("--evalue", default="0.001")
    p.add_argument("--sens", default="very-sensitive", help="ultra-sensitive: 3.3x slower, same hits here")
    p.add_argument("--block-size", default="2")
    p.add_argument("--threads", default="20")
    p.add_argument("--index-chunks", help="diamond -c; 1 = one pass over the block (needs more RAM)")
    p.add_argument("--out", default=os.path.expanduser(f"~/rep_newseq_{time.strftime('%F')}.csv"))
    a = p.parse_args()

    q_names = [l[1:].split()[0] for l in open(a.query) if l.startswith(">")]
    c = pymysql.connect(**blast_db.cfg(), autocommit=True).cursor()
    c.execute("SELECT nr_datum, dmnd_hash FROM blast_release WHERE quelle='nr' AND aktiv=1 "
              "ORDER BY nr_datum DESC LIMIT 1")
    nr_date, dhash = c.fetchone()
    c.execute("SELECT MAX(nr_datum) FROM blast_release WHERE quelle<>'nr'")
    delta_date = c.fetchone()[0]
    times = {}
    t_all = time.time()
    os.makedirs(TMP, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TMP) as d, open(LOCK, "w") as lock:
        t = time.time()
        fcntl.flock(lock, fcntl.LOCK_SH)               # no append while we read the .dmnd
        times["wait for lock"] = time.time() - t

        t = time.time()
        m = dmnd_preselect.preselect(c, new_days=a.days, threads=a.threads)
        times["pre-selection" + (" (cache hit)" if m["cache"] else "")] = time.time() - t
        log(f"pre-selection: {m['n_seq']} sequences, {m['letters']} letters, cache={m['cache']}")

        out, tf = os.path.join(d, "hits.tsv"), os.path.join(d, "rss.txt")
        cmd = [DIAMOND, "blastp", "--query", a.query, "--db", m["dmnd"], "--dbsize", str(m["dbsize"]),
               "--out", out, "--evalue", a.evalue, "--max-target-seqs", "0", f"--{a.sens}",
               "--threads", a.threads, "--block-size", a.block_size, "--tmpdir", d, "--outfmt", "6", *FIELDS]
        if a.index_chunks:
            cmd += ["--index-chunks", a.index_chunks]
        t = time.time()
        r = subprocess.run(["/usr/bin/time", "-f", "%M", "-o", tf, *cmd], capture_output=True, text=True)
        times["diamond blastp"] = time.time() - t
        if r.returncode != 0:
            raise RuntimeError("diamond: " + (r.stderr or r.stdout)[-1500:])
        rss_gb = int(open(tf).read().split()[-1]) / 1024 / 1024
        hits = [l.rstrip("\n").split("\t") for l in open(out)]

    t = time.time()
    meta = metadata(c, {h[1] for h in hits})
    times["metadata (MariaDB)"] = time.time() - t
    total = time.time() - t_all

    rows = []
    for h in hits:
        oid, ln, title, taxid, org, created, ncbi_date = meta.get(h[1], (None,) * 7)
        rows.append([h[0].split("_")[0], h[1], *h[2:], ln, taxid, org, created, ncbi_date, title])
    rows.sort(key=lambda x: (x[0], -float(x[11])))

    created = sorted(x[15] for x in rows if x[15])
    with open(a.out, "w", newline="") as f:
        f.write(f"# SARS-CoV-2 spike S1/S2 vs. NCBI nr proteins created within the last {a.days} days -- "
                f"DIAMOND blastp hits ({time.strftime('%F %T')})\n"
                f"# Query: {a.query} ({', '.join(q_names)})\n"
                f"# Database: nr release {nr_date:%F} + daily deltas up to {delta_date:%F}, pre-selected via MariaDB "
                f"(blast_seq_ncbi.createdate >= today - {a.days} d): {m['n_seq']:,} sequences, {m['letters']:,} letters\n"
                f"# Search: diamond blastp --{a.sens} --evalue {a.evalue} --max-target-seqs 0 --dbsize {m['dbsize']} "
                f"--block-size {a.block_size} --threads {a.threads}"
                + (f" --index-chunks {a.index_chunks}" if a.index_chunks else "") + "\n"
                "# Runtime: " + ", ".join(f"{k} {v:.1f} s" for k, v in times.items())
                + f", total {total:.1f} s; diamond peak RSS {rss_gb:.1f} GB\n"
                f"# Hits: {len(rows)}" + (f", subject creation dates {created[0]} .. {created[-1]}" if created else "")
                + "\n")
        w = csv.writer(f)
        w.writerow(["query", "subject_acc", "pident", "align_len", "mismatch", "gapopen", "qstart", "qend",
                    "sstart", "send", "evalue", "bitscore", "subject_len", "taxid", "organism",
                    "ncbi_createdate", "ncbi_last_modified", "title"])
        w.writerows(rows)

    # summary
    print(f"\nSpike S1/S2 vs. proteins created in the last {a.days} days  ({time.strftime('%F %T')})")
    print(f"database: nr {nr_date:%F} + deltas up to {delta_date:%F}; sub-.dmnd {m['n_seq']:,} sequences, "
          f"{m['letters']:,} letters")
    print("\nruntime")
    for k, v in times.items():
        print(f"  {k:<32} {v:8.1f} s")
    print(f"  {'total':<32} {total:8.1f} s   (diamond peak RSS {rss_gb:.1f} GB)")
    print(f"\n{'query':<6} {'hits':>6} {'>=90%':>7} {'50-90%':>7} {'<50%':>6} {'SARS-CoV-2':>11} {'other taxa':>11}")
    for q in sorted({x[0] for x in rows}):
        sel = [x for x in rows if x[0] == q]
        pid = [float(x[2]) for x in sel]
        n2 = sum(1 for x in sel if x[13] == SARS2)
        print(f"{q:<6} {len(sel):>6} {sum(p >= 90 for p in pid):>7} {sum(50 <= p < 90 for p in pid):>7} "
              f"{sum(p < 50 for p in pid):>6} {n2:>11} {len(sel) - n2:>11}")
    subj = {x[1]: x for x in rows}
    print(f"\ndistinct subject sequences: {len(subj)}")
    by_month = Counter(str(x[15])[:7] for x in subj.values() if x[15])
    print("by creation month: " + ", ".join(f"{k} {v}" for k, v in sorted(by_month.items())))
    other = Counter(x[14] or f"taxid {x[13]}" for x in subj.values() if x[13] != SARS2)
    if other:
        print("non-SARS-CoV-2 organisms: " + ", ".join(f"{k} ({v})" for k, v in other.most_common(15)))
    print(f"\nCSV: {a.out}")


if __name__ == "__main__":
    main()
