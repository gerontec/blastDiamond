#!/usr/bin/env python3
"""is_pathog for the sequences of one NCBI daily delivery: which of them are virulence factors or toxins.

The flags are per PROTEIN, not per organism. A new sequence gets them when it is homologous to a manually
curated reference protein (Swiss-Prot keyword Virulence KW-0843 or Toxin KW-0800, 12 481 entries,
blast_vf_ref). They are one bit mask - is_pathog = flags <> 0, is_toxic = the toxin bits - and blast_flag_bit
says what each bit means; it grows by itself when a new category turns up in the source. The row in
blast_pathogen_hit keeps the evidence - which reference protein, how identical, over how much of the sequence -
so the form can show why, instead of a bare yes.

The reference database is tiny (4.9 MB), so the search runs the other way round than a normal nr search: the new
sequences of the day are the query, vf.dmnd is the database. That is why this is cheap enough to run once a day.

  pathogen_scan.py --pending               every delivery of the last 30 days without a finished run (cron)
  pathogen_scan.py --day 2026-11-10        the new sequences of that delivery
  pathogen_scan.py --day yesterday         the delivery of the day before
  pathogen_scan.py --acc P0DTC2.1 ...      single accessions, nothing is written (the test of the chain)
  pathogen_scan.py --oids FILE             one OID per line, nothing is written

Thresholds (fixed here, written into blast_pathogen_run with every run): e-value <= 1e-20, at least 50 % identity
over at least 50 % of the new sequence. A hit of 90 % and more is practically the same protein; between 50 and
90 % it is a homolog and the annotation is transferred, which is a claim, not a measurement - that is what pident
in the row is for.

Env: VF_DMND (default /home/gh/diamond/vf/vf.dmnd), DIAMOND_BIN, PATHOGEN_TMP (default /mnt/archive/gh_tmp/pathogen).
"""
import argparse, datetime, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.expanduser("~/python"))
import blast_db, pymysql
from nr_parts import NrParts

VF = os.environ.get("VF_DMND", "/home/gh/diamond/vf/vf.dmnd")
DIAMOND = os.environ.get("DIAMOND_BIN", os.path.expanduser("~/build/diamond-master-61bbbe491bed/build/diamond"))
TMP = os.environ.get("PATHOGEN_TMP", "/mnt/archive/gh_tmp/pathogen")   # never /tmp, the files get large
MIN_EVALUE = "1e-20"
MIN_PIDENT = 50.0
MIN_QCOV = 50.0
THREADS = int(os.environ.get("PATHOGEN_THREADS", "16"))
T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:7.1f}s] {msg}", flush=True)


def write_fasta(nr, oids, path):
    n = 0
    with open(path, "w") as f:
        for oid, name, seq in nr.records(oids):
            if not seq:
                continue
            f.write(f">{oid}\n{seq}\n")
            n += 1
    return n


def run_diamond(query, out):
    cmd = [DIAMOND, "blastp", "--db", VF, "--query", query, "--out", out,
           "--outfmt", "6", "qseqid", "sseqid", "pident", "evalue", "bitscore", "qlen", "length",
           "--max-target-seqs", "1", "--evalue", MIN_EVALUE, "--id", str(MIN_PIDENT),
           "--query-cover", str(MIN_QCOV), "--threads", str(THREADS), "--quiet"]
    log(" ".join(cmd[:8]) + " ...")
    t = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"diamond failed ({p.returncode}): {p.stderr[-2000:]}")
    log(f"diamond done in {time.time() - t:.0f}s")


def best_hits(path):
    """best hit per OID: highest bitscore"""
    best = {}
    for line in open(path):
        f = line.rstrip("\n").split("\t")
        if len(f) < 7:
            continue
        oid, ref, pident, evalue, bits, qlen, alen = int(f[0]), f[1], float(f[2]), float(f[3]), float(f[4]), \
            int(f[5]), int(f[6])
        ref = ref.split("|")[1] if "|" in ref else ref        # sp|P0DTC2|SPIKE_SARS2 -> P0DTC2
        qcov = 100.0 * alen / qlen if qlen else None
        cur = best.get(oid)
        if cur is None or bits > cur[4]:
            best[oid] = (ref, pident, evalue, bits, bits, qcov)
    return {o: (r, p, e, b, q) for o, (r, p, e, b, _, q) in best.items()}


def ref_flags(con, accs):
    """bit mask of every reference protein that was hit (blast_vf_ref.flags, meaning in blast_flag_bit)"""
    if not accs:
        return {}
    c = con.cursor()
    out = {}
    accs = sorted(accs)
    for i in range(0, len(accs), 1000):
        part = accs[i:i + 1000]
        c.execute("SELECT acc, flags FROM blast_vf_ref WHERE acc IN (" +
                  ",".join(["%s"] * len(part)) + ")", part)
        for acc, fl in c.fetchall():
            out[acc] = int(fl)
    return out


def flag_names(con, mask):
    c = con.cursor()
    c.execute("SELECT GROUP_CONCAT(name ORDER BY bit_value) FROM blast_flag_bit WHERE %s & bit_value", (mask,))
    r = c.fetchone()
    return r[0] if r and r[0] else "(no category)"


def scan_day(con, datum):
    c = con.cursor()
    c.execute("SELECT COUNT(*) FROM blast_vf_ref")
    ref_n = c.fetchone()[0]
    if not ref_n:
        sys.exit("blast_vf_ref is empty - run vf_ref_build.py first")
    c.execute("INSERT INTO blast_pathogen_run (datum, started, ref_n, min_evalue, min_pident, min_qcov) "
              "VALUES (%s, NOW(), %s, %s, %s, %s)", (datum, ref_n, MIN_EVALUE, MIN_PIDENT, MIN_QCOV))
    run_id = c.lastrowid
    c.execute("SELECT oid FROM blast_seq_ncbi WHERE ncbi_datum = %s ORDER BY oid", (datum,))
    oids = [r[0] for r in c.fetchall()]
    log(f"{datum}: {len(oids)} new sequences")
    if not oids:
        c.execute("UPDATE blast_pathogen_run SET seconds = 0, n_new = 0, n_query = 0, n_hit = 0, "
                  "note = 'no sequences with this NCBI date' WHERE id = %s", (run_id,))
        return

    os.makedirs(TMP, exist_ok=True)
    q, o = f"{TMP}/{datum}.faa", f"{TMP}/{datum}.tsv"
    nr = NrParts(con.cursor())
    n_q = write_fasta(nr, oids, q)
    log(f"{n_q} sequences written to {q} ({os.path.getsize(q) / 1e6:.1f} MB)")
    run_diamond(q, o)
    best = best_hits(o)
    fl = ref_flags(con, {v[0] for v in best.values()})
    rows = [(oid, ref, pident, qcov, evalue, bits, fl.get(ref, 0), datum)
            for oid, (ref, pident, evalue, bits, qcov) in best.items()]
    for i in range(0, len(rows), 5000):
        c.executemany("INSERT INTO blast_pathogen_hit (oid, ref_acc, pident, qcov, evalue, bitscore, "
                      "flags, found) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                      "ON DUPLICATE KEY UPDATE ref_acc = IF(VALUES(bitscore) > bitscore, VALUES(ref_acc), ref_acc), "
                      "pident = IF(VALUES(bitscore) > bitscore, VALUES(pident), pident), "
                      "qcov = IF(VALUES(bitscore) > bitscore, VALUES(qcov), qcov), "
                      "evalue = IF(VALUES(bitscore) > bitscore, VALUES(evalue), evalue), "
                      "flags = IF(VALUES(bitscore) > bitscore, VALUES(flags), flags), "
                      "bitscore = GREATEST(bitscore, VALUES(bitscore))", rows[i:i + 5000])
    near = sum(1 for r in rows if r[2] >= 90)
    c.execute("UPDATE blast_pathogen_run SET seconds = %s, n_new = %s, n_query = %s, n_hit = %s WHERE id = %s",
              (int(time.time() - T0), len(oids), n_q, len(rows), run_id))
    log(f"{len(rows)} of {n_q} sequences flagged ({near} with at least 90 % identity, "
        f"{len(rows) - near} as homologs)")
    if rows:
        c.execute("""SELECT r.protein_name, r.organism, COUNT(*) n, MAX(h.pident) mx
                     FROM blast_pathogen_hit h JOIN blast_vf_ref r ON r.acc = h.ref_acc
                     WHERE h.found = %s GROUP BY r.protein_name, r.organism ORDER BY n DESC LIMIT 15""", (datum,))
        for name, org, n, mx in c.fetchall():
            log(f"  {n:7d}  up to {mx:5.1f} %  {(name or '')[:60]}  [{(org or '')[:40]}]")


def pending(con, days=30):
    """The deliveries of the last days that have no finished run yet: that way a day is not lost when the cron
    did not run, and a day that is already done is not scanned twice."""
    c = con.cursor()
    c.execute("""SELECT DISTINCT n.ncbi_datum FROM blast_seq_ncbi n
                 LEFT JOIN blast_pathogen_run r ON r.datum = n.ncbi_datum AND r.n_hit IS NOT NULL
                 WHERE r.id IS NULL AND n.ncbi_datum >= CURDATE() - INTERVAL %s DAY
                   AND n.ncbi_datum <= CURDATE()
                 ORDER BY n.ncbi_datum""", (days,))
    return [r[0].isoformat() for r in c.fetchall()]


def scan_accs(con, accs):
    """single accessions, nothing written: the chain end to end, e.g. the SARS-CoV-2 spike from nr"""
    c = con.cursor()
    oids, seen = [], {}
    for a in accs:
        base = a.split(".")[0]
        if "." in a:                                          # versioned accession: exact
            c.execute("SELECT oid, acc FROM blast_acc WHERE acc = %s", (a,))
        else:                                                 # unversioned: prefix, _ and % are LIKE wildcards
            like = base.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%") + ".%"
            c.execute("SELECT oid, acc FROM blast_acc WHERE acc LIKE %s", (like,))
        hit = c.fetchall()
        if not hit:
            log(f"{a}: not in blast_acc")
            continue
        oids.append(hit[0][0])
        seen[hit[0][0]] = hit[0][1]
    if not oids:
        return
    os.makedirs(TMP, exist_ok=True)
    q, o = f"{TMP}/check.faa", f"{TMP}/check.tsv"
    nr = NrParts(con.cursor())
    write_fasta(nr, oids, q)
    run_diamond(q, o)
    best = best_hits(o)
    for oid in oids:
        if oid not in best:
            log(f"{seen[oid]} (OID {oid}): is_pathog = 0 - no reference protein above the thresholds")
            continue
        ref, pident, evalue, bits, qcov = best[oid]
        c.execute("SELECT protein_name, organism, flags FROM blast_vf_ref WHERE acc = %s", (ref,))
        r = c.fetchone()
        mask = int(r[2]) if r else 0
        log(f"{seen[oid]} (OID {oid}): is_pathog = 1, flags = {mask} [{flag_names(con, mask)}], "
            f"{pident:.1f} % identical over {qcov:.0f} % of it, e-value {evalue:g}, "
            f"to {ref} {r[0] if r else ''} [{r[1] if r else ''}]")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", help="YYYY-MM-DD, or 'yesterday'")
    ap.add_argument("--pending", action="store_true",
                    help="every delivery of the last 30 days without a finished run")
    ap.add_argument("--acc", nargs="+", help="accessions, nothing is written")
    ap.add_argument("--oids", help="file with one OID per line, nothing is written")
    a = ap.parse_args()
    con = pymysql.connect(**blast_db.cfg(), autocommit=True)
    if a.pending:
        todo = pending(con)
        log(f"{len(todo)} deliveries without a finished run: {', '.join(todo) if todo else '-'}")
        for d in todo:
            scan_day(con, d)
    if a.day:
        d = a.day
        if d == "yesterday":
            d = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        scan_day(con, d)
    if a.acc:
        scan_accs(con, a.acc)
    if a.oids:
        scan_accs.__doc__                                     # oids path: reuse the writer directly
        oids = [int(l) for l in open(a.oids) if l.strip()]
        os.makedirs(TMP, exist_ok=True)
        nr = NrParts(con.cursor())
        write_fasta(nr, oids, f"{TMP}/oids.faa")
        run_diamond(f"{TMP}/oids.faa", f"{TMP}/oids.tsv")
        best = best_hits(f"{TMP}/oids.tsv")
        log(f"{len(best)} of {len(oids)} flagged")
    if not any([a.day, a.acc, a.oids, a.pending]):
        ap.print_help()
    log("done")
