#!/usr/bin/env python3
"""Calibration runs for the ad-hoc DIAMOND service: measures how long pre-selection and search take.

For every pre-selection (cached sub-.dmnd from dmnd_preselect) the search runs with several sensitivities and
query lengths. Runs that would only get slower (higher sensitivity, longer query on the same database) are
skipped once a run exceeded the per-run timeout. Results go to blast_calib (kind 'preselect' / 'search') and,
for searches, also to blast_ram_log so that the memory planning of the worker learns from them.

    diamond_calibrate.py                 # default plan, about 30-60 min on dell
    diamond_calibrate.py --timeout 600 --threads 20 --only-db new7,human
"""
import argparse, fcntl, json, os, random, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blast_db, pymysql
import dmnd_preselect
import blast_api_worker as W

NR_SEQ_PY = "/home/gh/iver_sim/mamba/envs/iver/bin/python"
NR_SEQ = "/home/gh/python/nr_seq.py"

DBS = {   # name -> pre-selection criteria, small to large
    "tiny": dict(taxon=[49511]),
    "dmel": dict(taxon=[7227]),
    "human": dict(taxon=[9606]),
    "mammalia": dict(taxon=[40674]),
    "fungi": dict(taxon=[4751]),
}
QUERIES = {"q100": ("YP_009724390.1", "319-418"), "q400": ("YP_009724390.1", "1-400"),
           "q1273": ("YP_009724390.1", None)}
SENS_ALL = ["fast", "mid-sensitive", "sensitive", "more-sensitive", "very-sensitive", "ultra-sensitive"]
SENS_LEN = ["fast", "sensitive", "very-sensitive", "ultra-sensitive"]

DDL = """CREATE TABLE IF NOT EXISTS blast_calib (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  run_id CHAR(24) NOT NULL,
  kind ENUM('preselect','search') NOT NULL,
  db_name VARCHAR(32) NOT NULL,
  criteria VARCHAR(255) NULL,
  n_seq BIGINT UNSIGNED NULL,
  db_letters BIGINT UNSIGNED NULL,
  mode ENUM('blastp','blastx') NULL,
  sensitivity VARCHAR(16) NULL,
  query_letters INT UNSIGNED NULL,
  threads SMALLINT UNSIGNED NULL,
  block_size DOUBLE NULL,
  seconds DECIMAL(10,1) NULL,
  ram_gb DECIMAL(6,2) NULL,
  ok TINYINT(1) NOT NULL,
  note VARCHAR(255) NULL,
  created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_calib (kind, mode, sensitivity)
) ENGINE=InnoDB COMMENT='timing measurements for the ad-hoc DIAMOND service (diamond_calibrate.py)'"""


def log(msg):
    print(time.strftime("[%F %T] ") + msg, flush=True)


def protein(acc, rng):
    cmd = [NR_SEQ_PY, NR_SEQ, acc] + (["--range", rng] if rng else [])
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout.replace("\n", "").strip()


CODON = {"A": "GCT", "R": "CGT", "N": "AAT", "D": "GAT", "C": "TGT", "Q": "CAA", "E": "GAA", "G": "GGT", "H": "CAT",
         "I": "ATT", "L": "CTG", "K": "AAA", "M": "ATG", "F": "TTT", "P": "CCG", "S": "TCT", "T": "ACT", "W": "TGG",
         "Y": "TAT", "V": "GTT", "X": "NNN"}


def run_search(c, run_id, dbname, m, mode, sens, qname, query, threads, timeout):
    q_letters = len(query)
    with tempfile.TemporaryDirectory(dir=W.TMP) as d:
        qf, of, tf = (os.path.join(d, x) for x in ("q.fa", "out.tsv", "rss.txt"))
        open(qf, "w").write(f">{qname}\n{query}\n")
        b, est, limit, free, source = W.ram_plan(c, mode, sens, q_letters, m["letters"])
        fields = [f for f in W.FIELDS if f != "staxids"]
        cmd = ["/usr/bin/time", "-f", "%M", "-o", tf, W.DIAMOND, mode, "--query", qf, "--out", of, "--evalue", "0.001",
               "--max-target-seqs", "25", f"--{sens}", "--threads", str(threads), "--tmpdir", d, "--outfmt", "6",
               *fields, "--db", m["dmnd"], "--dbsize", str(m["dbsize"]), "--block-size", str(b)]
        t0 = time.time()
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            ok, secs, note = r.returncode == 0, round(time.time() - t0, 1), None if r.returncode == 0 else r.stderr[-200:]
        except subprocess.TimeoutExpired:
            ok, secs, note = False, round(time.time() - t0, 1), f"timeout {timeout} s"
        try:
            ram = round(int(open(tf).read().split()[-1]) / 1048576, 2)
        except (OSError, ValueError, IndexError):
            ram = None
    c.execute("INSERT INTO blast_calib (run_id, kind, db_name, criteria, n_seq, db_letters, mode, sensitivity, "
              "query_letters, threads, block_size, seconds, ram_gb, ok, note) "
              "VALUES (%s,'search',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (run_id, dbname, json.dumps(DBS[dbname]), m["n_seq"], m["letters"], mode, sens, q_letters, threads, b,
               secs, ram, int(ok), note))
    if ok and ram is not None:
        c.execute("INSERT INTO blast_ram_log (job_id, mode, sensitivity, query_letters, db_letters, block_size, "
                  "eff_gletters, threads, ram_gb, est_gb, free_gb, seconds, ok) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)",
                  (run_id, mode, sens, q_letters, m["letters"], b, min(m["letters"] / 1e9, b), threads, ram, est, free, secs))
    log(f"  {dbname:6} {mode} {sens:16} {qname:6} {q_letters:5} letters: {secs:7.1f} s  {ram} GB  "
        f"block {b}{'' if ok else '  FAILED ' + (note or '')}")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--timeout", type=int, default=600, help="seconds per search run")
    ap.add_argument("--threads", type=int, default=20)
    ap.add_argument("--only-db", default="", help="comma-separated subset of " + ",".join(DBS))
    a = ap.parse_args()
    dbs = [x for x in (a.only_db.split(",") if a.only_db else DBS) if x]

    c = pymysql.connect(**blast_db.cfg(), autocommit=True).cursor()
    c.execute(DDL)
    run_id = "calib" + time.strftime("%Y%m%d%H%M%S") + "%05d" % random.randrange(100000)
    queries = {k: protein(*v) for k, v in QUERIES.items()}
    queries["nt1200"] = "".join(CODON.get(x, "NNN") for x in queries["q400"])
    log(f"run {run_id}: queries " + ", ".join(f"{k} {len(v)}" for k, v in queries.items()))

    with open(W.LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        for name in dbs:
            t0 = time.time()
            try:
                m = dmnd_preselect.preselect(c, **DBS[name], threads=str(a.threads))
                ok, note = True, ("cache" if m["cache"] else None)
            except (dmnd_preselect.TooLarge, dmnd_preselect.NoMatch) as e:
                m, ok, note = None, False, str(e)
            secs = round(time.time() - t0, 1)
            c.execute("INSERT INTO blast_calib (run_id, kind, db_name, criteria, n_seq, db_letters, threads, seconds, ok, note) "
                      "VALUES (%s,'preselect',%s,%s,%s,%s,%s,%s,%s,%s)",
                      (run_id, name, json.dumps(DBS[name]), m and m["n_seq"], m and m["letters"], a.threads, secs,
                       int(ok), note))
            log(f"{name}: pre-selection {secs} s" + (f", {m['n_seq']} sequences, {m['letters']} letters" if m else "")
                + (f" ({note})" if note else ""))
            if not m:
                continue
            plan = [("blastp", s, "q400") for s in SENS_ALL]
            plan += [("blastp", s, q) for q in ("q100", "q1273") for s in SENS_LEN]
            plan += [("blastx", s, "nt1200") for s in ("fast", "sensitive", "very-sensitive")]
            too_slow = {}            # (mode) -> lowest sensitivity index that timed out at q400
            for mode, sens, qname in plan:
                si = SENS_ALL.index(sens)
                if mode in too_slow and si >= too_slow[mode] and qname != "q100":
                    log(f"  {name:6} {mode} {sens:16} {qname:6} skipped (a cheaper run already timed out)")
                    continue
                if not run_search(c, run_id, name, m, mode, sens, qname, queries[qname], a.threads, a.timeout):
                    too_slow[mode] = min(too_slow.get(mode, 99), si)
    log("done")


if __name__ == "__main__":
    sys.exit(main())
