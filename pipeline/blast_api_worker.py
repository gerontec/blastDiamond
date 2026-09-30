#!/usr/bin/env python3
"""Runs the DIAMOND search jobs of the API (blast_job) one after another.
Holds a shared lock on /home/gh/.nr_dmnd.lock during a search so that diamond_append.py /
nr_daily_delta.py never change the .dmnd in the middle of a search.

Memory planning: before every search the need is estimated as BASE + k * min(DB letters, b) (in billions of
letters) and --block-size b is chosen so that it stays below min(RAM_MAX_GB, MemAvailable - RAM_RESERVE_GB).
k (GB per billion letters) comes from blast_ram_log: the peak RSS of earlier searches with the same mode and
sensitivity and a similar query size; without measurements the start values K_START apply. Every search runs
under /usr/bin/time and adds its own peak RSS to blast_ram_log."""
import os, sys, time, fcntl, tempfile, subprocess
import blast_db, pymysql
import dmnd_preselect
from dmnd_getseq import Dmnd

E = os.environ.get
DMND = E("DMND", "/home/gh/diamond/nr_full.dmnd")
DIAMOND = E("DIAMOND_BIN", os.path.expanduser("~/.local/share/mamba/envs/diamond/bin/diamond"))
LOCK = E("NR_LOCK", "/home/gh/.nr_dmnd.lock")
TMP = E("JOB_TMP", "/mnt/archive/blast_api_tmp")
TIMEOUT = int(E("JOB_TIMEOUT", "3600"))
THREADS = E("JOB_THREADS", "20")
RAM_MAX_GB = float(E("RAM_MAX_GB", "48"))          # upper limit for one search (operator's setting)
RAM_RESERVE_GB = float(E("RAM_RESERVE_GB", "4"))   # always left free for MariaDB & co.
RAM_WAIT_MIN = float(E("RAM_WAIT_MIN", "30"))      # wait this long for free memory, then fail
BASE_GB = 0.5                                      # diamond without a block (code, buffers, query)
BLOCKS = (2.0, 1.0, 0.5, 0.25, 0.1)                # --block-size steps, largest first (fewer blocks = faster)
# GB per billion letters in the block before any measurement exists. fast/sensitive from measurements of
# 2026-09-24/30 (small query, ~1.3 and ~1.8); the others are cautious assumptions until measurements replace them.
K_START = {"fast": 1.5, "mid-sensitive": 3.0, "sensitive": 3.0, "more-sensitive": 5.0,
           "very-sensitive": 8.0, "ultra-sensitive": 12.0}
FIELDS = "qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore staxids".split()


def log(msg):
    print(time.strftime("[%F %T] ") + msg, flush=True)


def db():
    return pymysql.connect(**blast_db.cfg(), autocommit=True)


def mem_available_gb():
    for z in open("/proc/meminfo"):
        if z.startswith("MemAvailable:"):
            return int(z.split()[1]) / 1048576
    return 0.0


def write_ram_status(c):
    """For the API (Apache cannot see /proc/meminfo because of ProcSubset=pid): one row in blast_ram_status."""
    m = {z.split(":")[0]: int(z.split()[1]) / 1048576 for z in open("/proc/meminfo") if z.split()[1:2]}
    c.execute("REPLACE INTO blast_ram_status (id, free_gb, total_gb, swap_free_gb, updated) VALUES (1,%s,%s,%s,NOW())",
              (round(m.get("MemAvailable", 0), 2), round(m.get("MemTotal", 0), 2), round(m.get("SwapFree", 0), 2)))


def estimate_k(c, mode, sens, q_letters):
    """GB per billion letters: largest measured value with the same mode and sensitivity and a query within
    q/4..4q (blastx: nucleotides, hence its own mode), else from larger queries, else the start value
    (doubled for large queries). Plus a 20 % margin."""
    for sql, args in (("query_letters BETWEEN %s AND %s", (q_letters / 4, q_letters * 4)),
                      ("query_letters >= %s", (q_letters,))):
        c.execute("SELECT MAX((ram_gb - %s) / eff_gletters), COUNT(*) FROM blast_ram_log "
                  f"WHERE ok = 1 AND mode = %s AND sensitivity = %s AND eff_gletters >= 0.05 AND {sql}",
                  (BASE_GB, mode, sens, *args))
        k, n = c.fetchone()
        if n:
            return max(float(k), 0.1) * 1.2, f"{n} measurements"
    k = K_START.get(sens, 12.0) * (2 if q_letters > 5000 else 1) * (2 if mode == "blastx" else 1)
    return k * 1.2, "start value"


def ram_plan(c, mode, sens, q_letters, db_letters):
    """-> (block_size, estimate_gb, limit_gb, free_gb, source_of_k). Waits if nothing fits."""
    k, source = estimate_k(c, mode, sens, q_letters)
    t0 = time.time()
    while True:
        free = mem_available_gb()
        limit = min(RAM_MAX_GB, free - RAM_RESERVE_GB)
        for b in BLOCKS:
            need = BASE_GB + k * min(db_letters / 1e9, b)
            if need <= limit:
                return b, round(need, 2), round(limit, 2), round(free, 2), source
            if db_letters / 1e9 <= b / 2:          # the DB already fits the next smaller block: smaller won't help
                break
        if time.time() - t0 > RAM_WAIT_MIN * 60:
            raise RuntimeError(f"not enough memory: need {need:.1f} GB at --block-size {b}, "
                               f"free {free:.1f} GB, limit {limit:.1f} GB")
        log(f"memory tight (free {free:.1f} GB, need from {need:.1f} GB), waiting")
        time.sleep(60)


def add_staxids(c, rows):
    """The sub-.dmnd has no taxonomy: taxa per hit from blast_acc (all accessions of the same OID)."""
    accs = sorted({r[3] for r in rows})
    tax = {}
    for i in range(0, len(accs), 500):
        part = accs[i:i + 500]
        c.execute("SELECT x.acc, GROUP_CONCAT(DISTINCT y.taxid ORDER BY y.taxid SEPARATOR ';') FROM blast_acc x "
                  "JOIN blast_acc y ON y.oid = x.oid WHERE x.acc IN (" + ",".join(["%s"] * len(part)) + ") "
                  "GROUP BY x.acc", part)
        tax.update(c.fetchall())
    return [(*r[:14], tax.get(r[3], "")) for r in rows]


def post_filter(c, rows, len_min, len_max, new_days):
    """Only for the full-search fallback: check length and creation date after the search."""
    if not (len_min or len_max or new_days):
        return rows
    ok = set()
    for i in range(0, len(rows), 500):
        part = sorted({r[3] for r in rows[i:i + 500]})
        c.execute("SELECT a.acc, s.len, n.createdate >= CURDATE() - INTERVAL %s DAY FROM blast_acc a "
                  "JOIN blast_seq s ON s.oid = a.oid LEFT JOIN blast_seq_ncbi n ON n.oid = a.oid "
                  "WHERE a.acc IN (" + ",".join(["%s"] * len(part)) + ")", [int(new_days or 0), *part])
        for acc, ln, is_new in c.fetchall():
            if (not len_min or ln >= len_min) and (not len_max or ln <= len_max) and (not new_days or is_new):
                ok.add(acc)
    return [(r[0], n, *r[2:]) for n, r in enumerate(x for x in rows if x[3] in ok)]


def run_job(c, job):
    job_id, mode, sens, evalue, max_t, taxonlist, query, len_min, len_max, new_days, q_letters = job
    os.makedirs(TMP, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TMP) as d:
        q, o = os.path.join(d, "q.fa"), os.path.join(d, "out.tsv")
        open(q, "w").write(query)
        cmd = [DIAMOND, mode, "--query", q, "--out", o, "--evalue", str(evalue),
               "--max-target-seqs", str(max_t), f"--{sens}", "--threads", THREADS, "--tmpdir", d,
               "--outfmt", "6", *FIELDS]
        t0 = time.time()
        pre, fallback, db_letters = None, False, None
        with open(LOCK, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)           # waits while an append is running
            c.execute("SELECT release_id, dmnd_hash FROM blast_release WHERE quelle='nr' AND aktiv=1 "
                      "ORDER BY nr_datum DESC LIMIT 1")
            rel, dhash = c.fetchone()
            if taxonlist or len_min or len_max or new_days:
                taxa = [int(x) for x in (taxonlist or "").split(",") if x]
                try:
                    m = dmnd_preselect.preselect(c, taxa, len_min, len_max, new_days, threads=THREADS)
                    cmd.remove("staxids")                  # sub-.dmnd without taxonomy, taxa come from blast_acc
                    cmd += ["--db", m["dmnd"], "--dbsize", str(m["dbsize"])]   # E-values as in a full search
                    pre = (f"{m['n_seq']} sequences, {m['letters']} letters, {m['seconds']} s"
                           + (" (cache)" if m["cache"] else ""))
                    db_letters = m["letters"]
                except dmnd_preselect.TooLarge as e:
                    cmd += ["--db", DMND] + (["--taxonlist", taxonlist] if taxonlist else [])
                    pre, fallback = f"full search, pre-selection {e}", True
            else:
                cmd += ["--db", DMND]
            if db_letters is None:
                db_letters = Dmnd(DMND).letters
            b, est, limit, free, source = ram_plan(c, mode, sens, q_letters, db_letters)
            log(f"{job_id}: memory free {free} GB, limit {limit} GB, estimate {est} GB ({source}), "
                f"--block-size {b}")
            tf = os.path.join(d, "rss.txt")
            t_diamond = time.time()
            r = subprocess.run(["/usr/bin/time", "-f", "%M", "-o", tf, *cmd, "--block-size", str(b)],
                               capture_output=True, text=True, timeout=TIMEOUT)
        secs = round(time.time() - t0, 1)
        try:
            ram = round(int(open(tf).read().split()[-1]) / 1048576, 2)
        except (OSError, ValueError, IndexError):
            ram = None
        if ram is not None:
            c.execute("INSERT INTO blast_ram_log (job_id, mode, sensitivity, query_letters, db_letters, block_size, "
                      "eff_gletters, threads, ram_gb, est_gb, free_gb, seconds, ok) "
                      "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (job_id, mode, sens, q_letters, db_letters, b, min(db_letters / 1e9, b), THREADS, ram,
                       est, free, round(time.time() - t_diamond, 1), int(r.returncode == 0)))
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout)[-1000:])
        rows = []
        for n, line in enumerate(open(o)):
            p = line.rstrip("\n").split("\t")
            rows.append((job_id, n, *p[:12], p[12] if len(p) > 12 else ""))
    if pre and not fallback:
        rows = add_staxids(c, rows)
    if fallback:
        rows = post_filter(c, rows, len_min, len_max, new_days)
    c.connection.begin()
    c.execute("DELETE FROM blast_job_hit WHERE job_id=%s", (job_id,))
    if rows:
        c.executemany("INSERT INTO blast_job_hit (job_id, n, qseqid, sseqid, pident, length, mismatch, gapopen, "
                      "qstart, qend, sstart, send, evalue, bitscore, staxids) "
                      "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
    c.execute("UPDATE blast_job SET status='done', fertig=NOW(), sekunden=%s, release_id=%s, dmnd_hash=%s, "
              "n_hits=%s, preselect=%s, block_size=%s, ram_est_gb=%s, ram_gb=%s, ram_free_gb=%s WHERE job_id=%s",
              (secs, rel, dhash, len(rows), pre, b, est, ram, free, job_id))
    c.connection.commit()
    log(f"{job_id}: {len(rows)} hits in {secs}s")


def main():
    c = db().cursor()
    c.execute("UPDATE blast_job SET status='queued', gestartet=NULL WHERE status='running'")  # after a crash
    last_clean = last_ram = 0
    while True:
        try:
            c.connection.ping(reconnect=True)
            if time.time() - last_ram > 30:
                write_ram_status(c)
                last_ram = time.time()
            if time.time() - last_clean > 600:
                c.execute("DELETE FROM blast_api_hits WHERE minute < %s", (int(time.time() // 60) - 10,))
                last_clean = time.time()
            c.execute("SELECT dmnd_hash FROM blast_release WHERE quelle='nr' AND aktiv=1 ORDER BY nr_datum DESC LIMIT 1")
            row = c.fetchone()
            if not row or row[0] is None:
                time.sleep(60)
                continue
            c.execute("SELECT job_id, mode, sensitivity, evalue, max_target_seqs, taxonlist, query, "
                      "len_min, len_max, new_days, query_letters FROM blast_job WHERE status='queued' ORDER BY erstellt LIMIT 1")
            job = c.fetchone()
            if not job:
                time.sleep(5)
                continue
            c.execute("UPDATE blast_job SET status='running', gestartet=NOW() WHERE job_id=%s AND status='queued'", (job[0],))
            log(f"{job[0]}: {job[1]} {job[2]} taxonlist={job[5]}")
            try:
                run_job(c, job)
            except Exception as e:
                try:
                    c.connection.rollback()
                except Exception:
                    pass
                c.connection.ping(reconnect=True)
                c.execute("UPDATE blast_job SET status='failed', fertig=NOW(), error=%s WHERE job_id=%s",
                          (f"{type(e).__name__}: {e}"[:2000], job[0]))
                log(f"{job[0]}: ERROR {type(e).__name__}: {e}")
        except pymysql.MySQLError as e:
            log(f"DB error: {e}")
            time.sleep(30)


if __name__ == "__main__":
    main()
