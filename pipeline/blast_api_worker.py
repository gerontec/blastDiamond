#!/usr/bin/env python3
"""Runs the DIAMOND search jobs of the API (blast_job) one after another.
Holds a shared lock on /home/gh/.nr_dmnd.lock during a search so that diamond_append.py /
nr_daily_delta.py never change the .dmnd in the middle of a search.

Tiers (blast_job.tier), all in this one queue, strictly one job at a time:
  key    API key holders (as before)
  adhoc  query form: runs only if it fits the time budget (budget_s, default 300 s). The pre-selection runs
         under a MariaDB statement time limit and a sequence cap; then the search time is predicted from the
         calibration runs (blast_calib, diamond_calibrate.py) and earlier searches (blast_ram_log). If the
         prediction does not fit the rest of the budget the job is 'rejected' instead of started; diamond itself
         is killed when the budget is used up. Ad-hoc jobs are taken before waiting key/full jobs.
  full   search of the whole nr by mail, one per day and address (enforced by api.php): waits as 'unconfirmed'
         until the link in the confirmation mail is opened, then runs like a key job; the result is mailed.
Results of adhoc/full jobs are also written as xlsx to RESULT_DIR (served by api.php ?r=jobxlsx).

Memory planning: before every search the need is estimated as BASE + k * min(DB letters, b) (in billions of
letters) and --block-size b is chosen so that it stays below min(RAM_MAX_GB, MemAvailable - RAM_RESERVE_GB).
k (GB per billion letters) comes from blast_ram_log: the peak RSS of earlier searches with the same mode and
sensitivity and a similar query size; without measurements the start values K_START apply. Every search runs
under /usr/bin/time and adds its own peak RSS to blast_ram_log."""
import os, sys, json, time, fcntl, tempfile, subprocess, signal, smtplib
from email.message import EmailMessage
from email.utils import formatdate
import blast_db, pymysql
import dmnd_preselect
from nr_parts import NrParts

E = os.environ.get
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

# ad-hoc service (query form)
ADHOC_SQL_S = int(E("ADHOC_SQL_S", "60"))           # MariaDB time limit for the pre-selection query
ADHOC_SAFETY = float(E("ADHOC_SAFETY", "1.5"))      # prediction margin
ADHOC_AVG_LEN = 250                                 # residues per sequence, to turn a letter cap into a sequence cap
RESULT_DIR = E("RESULT_DIR", "/var/lib/blast_results")
RESULT_KEEP_DAYS = 14
CONFIRM_HOURS = 24
SMTP_HOST = E("BLAST_SMTP_HOST", "")                # empty = no mail
MAIL_FROM = E("BLAST_MAIL_FROM", "blast@localhost")
PUBLIC_URL = E("BLAST_PUBLIC_URL", "http://localhost").rstrip("/")


class Rejected(Exception):
    """An ad-hoc job that would not fit its time budget: not run, status 'rejected'."""


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


Q_MIN = 1300   # below this query length the search time hardly depends on it (Mammalia fast: 100 aa 12.9 s, 1273 aa 13.0 s)


def search_rate(c, mode, sens, db_letters=None):
    """Seconds per (query letter x billion DB letters), the largest value measured for mode and sensitivity:
    calibration runs (blast_calib, timeouts count as lower bounds) and earlier searches (blast_ram_log).
    Query lengths below Q_MIN count as Q_MIN. With db_letters only runs on databases of at least a quarter of
    that size count (on small databases diamond's start-up time dominates and inflates the rate); without
    such runs all count. Only runs with q x L >= 0.01 G count. None = no data."""
    sql = ("SELECT MAX(r) FROM ("
           " SELECT (seconds - 1) / (GREATEST(query_letters, %s) * db_letters / 1e9) AS r FROM blast_calib"
           "  WHERE kind = 'search' AND mode = %s AND sensitivity = %s AND query_letters * db_letters >= 1e7"
           "  AND (ok = 1 OR note LIKE 'timeout%%') AND db_letters >= %s"
           " UNION ALL SELECT (seconds - 1) / (GREATEST(query_letters, %s) * db_letters / 1e9) FROM blast_ram_log"
           "  WHERE ok = 1 AND mode = %s AND sensitivity = %s AND query_letters * db_letters >= 1e7"
           "  AND db_letters >= %s) x")
    for floor in ((db_letters / 4, 0) if db_letters else (0,)):
        c.execute(sql, (Q_MIN, mode, sens, floor, Q_MIN, mode, sens, floor))
        r = c.fetchone()[0]
        if r is not None:
            return float(r)
    return None


def build_rate(c):
    """Seconds per sequence to build a sub-.dmnd that is not cached yet (MariaDB selection + reading the records
    from nr_full.dmnd + makedb), the largest value of the calibration builds with at least 100 000 sequences.
    Default 60 us per sequence (2026-11-10: Mammalia, 15.5 M sequences in 776 s = 50 us)."""
    c.execute("SELECT MAX(seconds / n_seq) FROM blast_calib WHERE kind = 'preselect' AND ok = 1 AND note IS NULL "
              "AND n_seq >= 100000")
    r = c.fetchone()[0]
    return float(r) if r is not None else 60e-6


def predict_seconds(c, mode, sens, q_letters, db_letters):
    """Predicted diamond wall time (with ADHOC_SAFETY margin), None without measurements."""
    rate = search_rate(c, mode, sens, db_letters)
    if rate is None:
        return None
    return round((1 + max(rate, 0) * max(q_letters, Q_MIN) * db_letters / 1e9) * ADHOC_SAFETY, 1)


def ram_plan(c, mode, sens, q_letters, db_letters, wait_min=None):
    """-> (block_size, estimate_gb, limit_gb, free_gb, source_of_k). Waits if nothing fits
    (at most wait_min minutes, default RAM_WAIT_MIN)."""
    k, source = estimate_k(c, mode, sens, q_letters)
    t0 = time.time()
    wait_min = RAM_WAIT_MIN if wait_min is None else wait_min
    while True:
        free = mem_available_gb()
        limit = min(RAM_MAX_GB, free - RAM_RESERVE_GB)
        for b in BLOCKS:
            need = BASE_GB + k * min(db_letters / 1e9, b)
            if need <= limit:
                return b, round(need, 2), round(limit, 2), round(free, 2), source
            if db_letters / 1e9 <= b / 2:          # the DB already fits the next smaller block: smaller won't help
                break
        if time.time() - t0 >= wait_min * 60:
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
    """Only for the full-search fallback: check length and NCBI date after the search."""
    if not (len_min or len_max or new_days):
        return rows
    ok = set()
    for i in range(0, len(rows), 500):
        part = sorted({r[3] for r in rows[i:i + 500]})
        c.execute("SELECT a.acc, s.len, n.ncbi_datum >= CURDATE() - INTERVAL %s DAY FROM blast_acc a "
                  "JOIN blast_seq s ON s.oid = a.oid LEFT JOIN blast_seq_ncbi n ON n.oid = a.oid "
                  "WHERE a.acc IN (" + ",".join(["%s"] * len(part)) + ")", [int(new_days or 0), *part])
        for acc, ln, is_new in c.fetchall():
            if (not len_min or ln >= len_min) and (not len_max or ln <= len_max) and (not new_days or is_new):
                ok.add(acc)
    return [(r[0], n, *r[2:]) for n, r in enumerate(x for x in rows if x[3] in ok)]


def run_limited(cmd, timeout):
    """subprocess.run with a timeout that kills the whole process group (diamond runs under /usr/bin/time:
    killing only the direct child would leave diamond running)."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.communicate()
        raise
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


PART_ROOT = {"p1_mammalia": 40674}     # database selection (blast_job.db) -> root taxid, as DBS in api.php


def part_db(c, name, nr):
    """(dmnd, letters, n_seq, nr_letters, dmnd_hash) of a built part, or None while it is not built yet. nr_letters
    (--dbsize, E-values as in a search of the whole nr) is the size of the whole nr (nr_parts.NR_JSON)."""
    c.execute("SELECT dmnd, letters, n_seq, dmnd_hash FROM blast_part WHERE part=%s", (name,))
    row = c.fetchone()
    if not row or not os.path.exists(row[0]):
        return None
    return row[0], int(row[1]), int(row[2]), nr.letters, row[3]


def merge_hits(rows, max_t, query_ids=()):
    """Hits of the searches of several parts: a sequence stored in more than one part counts once (best bit score),
    then per query the best max_t subjects by e-value / bit score, renumbered. Queries keep their input order."""
    best, order = {}, {q: i for i, q in enumerate(query_ids)}
    for r in rows:
        q, s = r[2], r[3]
        order.setdefault(q, len(order))
        k = (q, s)
        if k not in best or float(r[13]) > float(best[k][13]):
            best[k] = r
    per_q = {}
    for (q, _), r in best.items():
        per_q.setdefault(q, []).append(r)
    out = []
    for q in sorted(per_q, key=order.get):
        hs = sorted(per_q[q], key=lambda r: (float(r[12]), -float(r[13])))[:max_t]
        out += hs
    return [(r[0], n, *r[2:]) for n, r in enumerate(out)]


def run_job(c, job):
    (job_id, mode, sens, evalue, max_t, taxonlist, query, len_min, len_max, new_days, q_letters, tier, budget,
     dbsel) = job
    adhoc = tier == "adhoc"
    budget = budget or 300
    os.makedirs(TMP, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TMP) as d:
        q, o = os.path.join(d, "q.fa"), os.path.join(d, "out.tsv")
        open(q, "w").write(query)
        cmd = [DIAMOND, mode, "--query", q, "--out", o, "--evalue", str(evalue),
               "--max-target-seqs", str(max_t), f"--{sens}", "--threads", THREADS, "--tmpdir", d,
               "--outfmt", "6", *FIELDS]
        t0 = time.time()
        pre, fallback, db_letters = None, False, None
        whole = None        # search of the whole nr: list of (part, dmnd, letters), one diamond run per part
        with open(LOCK, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)           # waits while an append is running
            c.execute("SELECT release_id, dmnd_hash FROM blast_release WHERE quelle='nr' AND aktiv=1 "
                      "ORDER BY nr_datum DESC LIMIT 1")
            rel, dhash = c.fetchone()
            nr = NrParts(c)
            rel, dhash = nr.release_id, nr.hash          # current state of the parts, not the 2026-09 nr release
            part = None
            if dbsel:
                # built part and no length/date filter: search the part itself; otherwise (part not built yet,
                # or filters that need a pre-selection) restrict to the part's taxa by pre-selection
                part = None if (len_min or len_max or new_days) else part_db(c, dbsel, nr)
                if part is None and not taxonlist:
                    taxonlist = str(PART_ROOT[dbsel])
            if part:
                pdmnd, db_letters, pn, nr_letters, dhash = part   # the job records the part's hash
                cmd += ["--db", pdmnd, "--dbsize", str(nr_letters)] + (["--taxonlist", taxonlist] if taxonlist else [])
                pre = f"database {dbsel}: {pn} sequences, {db_letters} letters"
            elif adhoc and not (taxonlist or len_min or len_max or new_days):
                raise Rejected("a search of the whole nr cannot run ad hoc; restrict organism or length, "
                               "or request a full search by mail")
            elif taxonlist or len_min or len_max or new_days:
                taxa = [int(x) for x in (taxonlist or "").split(",") if x]
                ps_kw = {}
                if adhoc:   # cap the pre-selection at what the time budget could search at all
                    rate = search_rate(c, mode, sens)
                    if rate is None:
                        raise Rejected(f"no timing measurements for {mode} {sens} yet, so the search time "
                                       "cannot be predicted")
                    # two caps: what the search could cover in the budget, and what can be built in 60 % of it
                    # (building cannot be interrupted; a cached pre-selection skips the build and both caps)
                    max_letters = (budget - 1) / ADHOC_SAFETY / max(rate, 1e-9) / max(q_letters, Q_MIN) * 1e9
                    max_build = budget * 0.6 / ADHOC_SAFETY / build_rate(c)
                    ps_kw["max_seq"] = max(1000, min(dmnd_preselect.MAX_SEQ, int(max_letters / ADHOC_AVG_LEN),
                                                     int(max_build)))
                    c.execute("SET SESSION max_statement_time = %s", (ADHOC_SQL_S,))
                try:
                    m = dmnd_preselect.preselect(c, taxa, len_min, len_max, new_days, threads=THREADS, **ps_kw)
                    cmd.remove("staxids")                  # sub-.dmnd without taxonomy, taxa come from blast_acc
                    cmd += ["--db", m["dmnd"], "--dbsize", str(m["dbsize"])]   # E-values as in a full search
                    pre = (f"{m['n_seq']} sequences, {m['letters']} letters, {m['seconds']} s"
                           + (" (cache)" if m["cache"] else ""))
                    db_letters = m["letters"]
                except dmnd_preselect.TooLarge as e:
                    if adhoc:
                        raise Rejected(f"the pre-selection is too large for a search within {budget} s ({e}); "
                                       "narrow organism or length, or request a full search by mail")
                    whole = nr.dbs()
                    pre, fallback = f"full search, pre-selection {e}", True
                except pymysql.err.OperationalError as e:
                    if adhoc and e.args and e.args[0] == 1969:      # max_statement_time exceeded
                        raise Rejected(f"the pre-selection took longer than {ADHOC_SQL_S} s; choose a smaller "
                                       "organism group or add a length window")
                    raise
                except dmnd_preselect.NoMatch as e:      # nothing to search: done with 0 hits
                    c.execute("UPDATE blast_job SET status='done', fertig=NOW(), sekunden=%s, release_id=%s, "
                              "dmnd_hash=%s, n_hits=0, preselect=%s WHERE job_id=%s",
                              (round(time.time() - t0, 1), rel, dhash, str(e), job_id))
                    log(f"{job_id}: {e}")
                    return
                finally:
                    if adhoc:
                        try:
                            c.execute("SET SESSION max_statement_time = 0")
                        except pymysql.MySQLError:
                            c.connection.ping(reconnect=True)
            else:
                whole = nr.dbs()
            if whole:
                # one run per part, E-values as in a search of the whole nr; the taxon filter applies in every part
                db_letters = sum(p[2] for p in whole)
                runs = [(p[0], ["--db", p[1], "--dbsize", str(nr.letters)]
                         + (["--taxonlist", taxonlist] if taxonlist else []), p[2]) for p in whole]
                pre = (pre + "; " if pre else "") + f"whole nr in {len(whole)} parts, {nr.letters} letters"
            else:
                runs = [(dbsel or "preselect", [], db_letters)]
            try:   # memory: the largest single database counts, the parts are searched one after another
                b, est, limit, free, source = ram_plan(c, mode, sens, q_letters, max(r[2] for r in runs),
                                                       wait_min=0 if adhoc else None)
            except RuntimeError as e:
                if adhoc:
                    raise Rejected(f"the server is busy ({e}); please try again later")
                raise
            log(f"{job_id}: memory free {free} GB, limit {limit} GB, estimate {est} GB ({source}), "
                f"--block-size {b}")
            pred = predict_seconds(c, mode, sens, q_letters, db_letters)
            c.execute("UPDATE blast_job SET est_seconds=%s, preselect=%s WHERE job_id=%s", (pred, pre, job_id))
            timeout = TIMEOUT
            if adhoc:
                elapsed = time.time() - t0
                if pred is None:
                    raise Rejected(f"no timing measurements for {mode} {sens} yet")
                if elapsed + pred > budget:
                    raise Rejected(f"predicted search time {pred:.0f} s (plus {elapsed:.0f} s pre-selection) exceeds "
                                   f"the limit of {budget} s; narrow organism or length, use a lower sensitivity, "
                                   "or request a full search by mail")
                timeout = max(5, budget - elapsed)
                log(f"{job_id}: ad hoc, predicted {pred} s, {elapsed:.0f} s used, timeout {timeout:.0f} s")
            tf = os.path.join(d, "rss.txt")
            t_run = time.time()
            rows, ram = [], None
            for name, db_args, letters in runs:
                t_diamond = time.time()
                left = timeout - (t_diamond - t_run)
                if left <= 0:
                    raise RuntimeError(f"stopped after {timeout:.0f} s before part {name}"
                                       + (f": time budget of {budget} s used up" if adhoc else ""))
                try:
                    r = run_limited(["/usr/bin/time", "-f", "%M", "-o", tf, *cmd, *db_args, "--block-size", str(b)],
                                    left)
                except subprocess.TimeoutExpired:
                    raise RuntimeError(f"stopped after {timeout:.0f} s in part {name}"
                                       + (f": time budget of {budget} s used up (the prediction was too low)"
                                          if adhoc else ""))
                try:
                    rss = round(int(open(tf).read().split()[-1]) / 1048576, 2)
                except (OSError, ValueError, IndexError):
                    rss = None
                if rss is not None:
                    ram = max(ram or 0, rss)
                    c.execute("INSERT INTO blast_ram_log (job_id, mode, sensitivity, query_letters, db_letters, "
                              "block_size, eff_gletters, threads, ram_gb, est_gb, free_gb, seconds, ok) "
                              "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                              (job_id, mode, sens, q_letters, letters, b, min(letters / 1e9, b), THREADS, rss,
                               est, free, round(time.time() - t_diamond, 1), int(r.returncode == 0)))
                if r.returncode != 0:
                    raise RuntimeError(f"{name}: " + (r.stderr or r.stdout)[-1000:])
                for line in open(o):
                    p = line.rstrip("\n").split("\t")
                    rows.append((job_id, len(rows), *p[:12], p[12] if len(p) > 12 else ""))
                if whole:
                    log(f"{job_id}: part {name}: {len(rows)} hits so far, {time.time() - t_diamond:.0f} s")
        secs = round(time.time() - t0, 1)
    if whole:
        rows = merge_hits(rows, int(max_t), [l[1:].split()[0] for l in query.splitlines() if l.startswith(">") and l[1:].split()])
    if (pre and not fallback) or whole:   # parts carry only their own accessions' taxa: take all from blast_acc
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


# ------------------------------------------------------------- results of form jobs (xlsx) and mail

def taxon_names(c, taxids):
    taxids = sorted({int(t) for t in taxids if str(t).isdigit()})
    names = {}
    for i in range(0, len(taxids), 500):
        part = taxids[i:i + 500]
        c.execute("SELECT taxid, name FROM blast_taxon WHERE taxid IN (" + ",".join(["%s"] * len(part)) + ")", part)
        names.update(c.fetchall())
    return names


def write_xlsx(c, job_id):
    """Hits of a form job as xlsx (opens on any Windows PC): sheets 'info' and 'hits'."""
    from openpyxl import Workbook
    c.execute("SELECT j.tier, j.mode, j.sensitivity, j.evalue, j.max_target_seqs, j.taxonlist, j.len_min, j.len_max, "
              "j.new_days, j.n_query, j.query_letters, j.erstellt, j.sekunden, j.n_hits, j.preselect, j.dmnd_hash, "
              "r.nr_datum, j.db FROM blast_job j LEFT JOIN blast_release r ON r.release_id = j.release_id WHERE j.job_id=%s",
              (job_id,))
    (tier, mode, sens, evalue, max_t, taxonlist, lmin, lmax, new_days, n_query, q_letters, created, secs, n_hits,
     pre, dhash, nr_date, dbsel) = c.fetchone()
    c.execute("SELECT h.qseqid, h.sseqid, s.title, h.staxids, h.pident, h.length, h.mismatch, h.gapopen, h.qstart, "
              "h.qend, h.sstart, h.send, h.evalue, h.bitscore FROM blast_job_hit h "
              "LEFT JOIN blast_acc a ON a.acc = h.sseqid LEFT JOIN blast_seq s ON s.oid = a.oid "
              "WHERE h.job_id=%s ORDER BY h.n", (job_id,))
    hits = c.fetchall()
    names = taxon_names(c, [t for h in hits for t in (h[3] or "").split(";")] + (taxonlist or "").split(","))
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("info")
    for k, v in [("job", job_id), ("created", str(created)), ("program", f"DIAMOND {mode}, {sens}"),
                 ("e-value", evalue), ("max. hits per query", max_t),
                 ("database selection", f"{dbsel} (taxon {PART_ROOT.get(dbsel, '')})" if dbsel else "whole nr"),
                 ("organism filter", ", ".join(f"{names.get(int(t), t)} ({t})" for t in (taxonlist or "").split(",") if t)
                  or "none"),
                 ("length filter", f"{lmin or ''}..{lmax or ''}" if (lmin or lmax) else "none"),
                 ("new within days", new_days or "none"), ("queries", n_query), ("query letters", q_letters),
                 ("database", f"NCBI nr as of {nr_date}, DIAMOND file {dhash}"), ("pre-selection", pre or "none"),
                 ("search seconds", float(secs or 0)), ("hits", n_hits),
                 ("source", f"{PUBLIC_URL}/  ·  https://github.com/gerontec/blastDiamond")]:
        ws.append([k, v])
    ws = wb.create_sheet("hits")
    ws.append(["query", "subject", "title", "organisms", "identity %", "alignment length", "mismatches", "gap openings",
               "query start", "query end", "subject start", "subject end", "e-value", "bit score", "taxids"])
    for h in hits:
        org = "; ".join(names.get(int(t), t) for t in (h[3] or "").split(";") if t.isdigit())
        ws.append([h[0], h[1], (h[2] or "")[:32000], org, float(h[4]), int(h[5]), int(h[6]), int(h[7]), int(h[8]),
                   int(h[9]), int(h[10]), int(h[11]), float(h[12]), float(h[13]), h[3]])
    os.makedirs(RESULT_DIR, exist_ok=True)
    tmp = os.path.join(RESULT_DIR, job_id + ".xlsx.part")
    wb.save(tmp)
    os.chmod(tmp, 0o644)
    os.replace(tmp, os.path.join(RESULT_DIR, job_id + ".xlsx"))


def send_mail(to, subject, body):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Date"] = MAIL_FROM, to, subject, formatdate(localtime=True)
    msg.set_content(body + f"\n-- \n{PUBLIC_URL}/  (source: https://github.com/gerontec/blastDiamond)\n")
    with smtplib.SMTP(SMTP_HOST, 25, timeout=30) as s:
        s.send_message(msg)


NEWEST_TAXID, NEWEST_N = 9606, 60
NEWEST_MIN_LEN = 10          # only sequences longer than this: 2-5 aa entries (e.g. Ig/TCR "diversity" segments) align with nothing
_newest_at = 0


def write_newest(c):
    """Cache file for the form: the NEWEST_N sequences of Homo sapiens longer than NEWEST_MIN_LEN most recently
    added to nr (highest OIDs; daily deltas are appended at the end), with title, length and NCBI date. Rewritten
    every hour. Reads blast_acc in batches along its (taxid, oid) index and filters the length per batch; a JOIN
    with a length condition makes MariaDB scan instead (> 20 s)."""
    import json
    items, last_oid, batch = [], None, NEWEST_N * 2
    for _ in range(50):
        if last_oid is None:
            c.execute("SELECT oid, acc FROM blast_acc WHERE taxid = %s ORDER BY oid DESC LIMIT %s", (NEWEST_TAXID, batch))
        else:
            c.execute("SELECT oid, acc FROM blast_acc WHERE taxid = %s AND oid < %s ORDER BY oid DESC LIMIT %s",
                      (NEWEST_TAXID, last_oid, batch))
        accs = dict(c.fetchall())
        if not accs:
            break
        last_oid = min(accs)
        c.execute("SELECT oid, len, title, quelle, datei, hinzugefuegt, ncbi_datum FROM blast_seq_herkunft "
                  "WHERE oid IN (" + ",".join(["%s"] * len(accs)) + ") AND len > %s ORDER BY oid DESC",
                  list(accs) + [NEWEST_MIN_LEN])
        for oid, ln, title, src, file, added, ncbi_date in c.fetchall():
            items.append({"acc": accs[oid], "oid": oid, "length": ln, "title": title, "source": src, "file": file,
                          "added": str(added) if added else None, "ncbi_date": str(ncbi_date) if ncbi_date else None})
        if len(items) >= NEWEST_N:
            break
    items = items[:NEWEST_N]
    os.makedirs(RESULT_DIR, exist_ok=True)
    tmp = os.path.join(RESULT_DIR, ".newest_human.json.part")
    json.dump({"taxid": NEWEST_TAXID, "organism": "Homo sapiens", "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
               "items": items}, open(tmp, "w"), ensure_ascii=False)
    os.chmod(tmp, 0o644)
    os.replace(tmp, os.path.join(RESULT_DIR, "newest_human.json"))


PATHOGEN_N = 60
PATHOGEN_MIN_PIDENT = 85.0   # below that, a conserved enzyme without any role in an infection reaches the reference protein


def write_newest_pathogen(c):
    """Cache file for the form: the PATHOGEN_N nr sequences most recently flagged (blast_pathogen_hit, filled by
    the daily pathogen_scan.py), with the reference protein the flag comes from and how identical it is.

    Inclusion rule: ONE set category of the bit mask is enough (flags <> 0) - a sequence does not have to be a
    toxin as well as a virulence factor, and no category is preferred over another. The categories themselves are
    listed per row in 'kinds', so the reader sees which one it was.

    The flag is per protein, not per organism: the spike of SARS-CoV-2 is in, its nucleocapsid is not."""
    import json
    c.execute("SELECT bit_value, name, class FROM blast_flag_bit ORDER BY bit_value")
    bits = [(int(b), n, cl) for b, n, cl in c.fetchall()]
    c.execute("""SELECT h.oid, a.acc, a.taxid, t.name, s.len, s.title, s.ncbi_datum,
                        h.ref_acc, h.pident, h.qcov, h.evalue, h.flags,
                        r.protein_name, r.organism
                 FROM blast_pathogen_hit h
                   JOIN blast_acc a ON a.oid = h.oid AND a.pos = 0
                   JOIN blast_seq_herkunft s ON s.oid = h.oid
                   JOIN blast_vf_ref r ON r.acc = h.ref_acc
                   LEFT JOIN blast_taxon t ON t.taxid = a.taxid
                 WHERE h.flags <> 0 AND h.pident >= %s
                 ORDER BY h.found DESC, h.oid DESC LIMIT %s""", (PATHOGEN_MIN_PIDENT, PATHOGEN_N))
    items = [{"acc": acc, "oid": oid, "taxid": taxid, "organism": org, "length": ln, "title": title,
              "ncbi_date": str(nd) if nd else None, "ref_acc": ref, "pident": float(pid),
              "qcov": float(qcov) if qcov is not None else None, "evalue": float(ev), "flags": int(fl),
              "kinds": [n for b, n, _ in bits if int(fl) & b],
              "toxic": any(int(fl) & b for b, _, cl in bits if cl == "toxin"),
              "ref_name": rname, "ref_organism": rorg}
             for (oid, acc, taxid, org, ln, title, nd, ref, pid, qcov, ev, fl, rname, rorg) in c.fetchall()]
    c.execute("SELECT COUNT(*) FROM blast_vf_ref")
    ref_n = c.fetchone()[0]
    c.execute("SELECT datum, n_new, n_hit FROM blast_pathogen_run WHERE n_hit IS NOT NULL "
              "ORDER BY datum DESC, id DESC LIMIT 1")
    last = c.fetchone()
    os.makedirs(RESULT_DIR, exist_ok=True)
    tmp = os.path.join(RESULT_DIR, ".newest_pathogen.json.part")
    json.dump({"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "reference_proteins": ref_n,
               "min_pident": PATHOGEN_MIN_PIDENT,
               "rule": "a sequence is listed as soon as one category of its bit mask is set (flags <> 0)",
               "categories": [{"bit": b, "name": n, "class": cl} for b, n, cl in bits],
               "last_scan": {"date": str(last[0]), "new_sequences": last[1], "flagged": last[2]} if last else None,
               "items": items}, open(tmp, "w"), ensure_ascii=False)
    os.chmod(tmp, 0o644)
    os.replace(tmp, os.path.join(RESULT_DIR, "newest_pathogen.json"))


def housekeeping(c):
    """Confirmation and result mails of full searches, expiry of unconfirmed requests, old result files,
    hourly cache of the newest human sequences."""
    global _newest_at
    if time.time() - _newest_at > 3600:
        try:
            write_newest(c)
        except Exception as e:                    # the cache is a convenience, never stop the worker for it
            log(f"newest cache failed: {e}")
        try:
            write_newest_pathogen(c)
        except Exception as e:
            log(f"pathogen cache failed: {e}")
        _newest_at = time.time()
    c.execute("UPDATE blast_job SET status='rejected', fertig=NOW(), error='not confirmed within %s hours' "
              "WHERE tier='full' AND status='unconfirmed' AND erstellt < NOW() - INTERVAL %s HOUR",
              (CONFIRM_HOURS, CONFIRM_HOURS))
    if SMTP_HOST:
        c.execute("SELECT job_id, email, confirm_token, query_letters, n_query FROM blast_job "
                  "WHERE tier='full' AND status='unconfirmed' AND confirm_mailed IS NULL")
        for jid, email, token, letters, n in c.fetchall():
            link = f"{PUBLIC_URL}/?confirm={jid}&token={token}"
            try:
                send_mail(email, "Confirm your full DIAMOND search / Vollsuche bestätigen",
                          f"Please open this link within {CONFIRM_HOURS} hours to start the search of the whole NCBI nr "
                          f"({n} sequence(s), {letters} letters):\n\n  {link}\n\n"
                          f"Bitte öffnen Sie den Link innerhalb von {CONFIRM_HOURS} Stunden, um die Suche über die "
                          f"gesamte NCBI nr zu starten.\n\nIf you did not ask for this, ignore this mail. / "
                          "Falls Sie das nicht angefordert haben, ignorieren Sie diese Mail.\n")
                c.execute("UPDATE blast_job SET confirm_mailed=NOW() WHERE job_id=%s", (jid,))
                log(f"{jid}: confirmation mail sent")
            except (OSError, smtplib.SMTPException) as e:
                log(f"{jid}: confirmation mail failed: {e}")
        c.execute("SELECT job_id, email, status, n_hits, sekunden, error FROM blast_job WHERE tier='full' "
                  "AND status IN ('done','failed') AND result_mailed IS NULL AND email IS NOT NULL")
        for jid, email, status, n_hits, secs, err in c.fetchall():
            if status == "done":
                body = (f"Your full DIAMOND search is finished: {n_hits} hits in {float(secs or 0) / 60:.0f} min.\n"
                        f"Ihre Vollsuche ist fertig: {n_hits} Treffer.\n\n"
                        f"Excel file / Excel-Datei (kept {RESULT_KEEP_DAYS} days):\n  {PUBLIC_URL}/api.php?r=jobxlsx&id={jid}\n\n"
                        f"Results in the browser / im Browser:\n  {PUBLIC_URL}/?job={jid}\n")
                subject = f"DIAMOND full search done: {n_hits} hits"
            else:
                body = f"Your full DIAMOND search failed / Ihre Vollsuche ist fehlgeschlagen:\n\n  {err}\n"
                subject = "DIAMOND full search failed"
            try:
                send_mail(email, subject, body)
                c.execute("UPDATE blast_job SET result_mailed=NOW() WHERE job_id=%s", (jid,))
                log(f"{jid}: result mail sent")
            except (OSError, smtplib.SMTPException) as e:
                log(f"{jid}: result mail failed: {e}")
    if os.path.isdir(RESULT_DIR):
        limit = time.time() - RESULT_KEEP_DAYS * 86400
        for f in os.listdir(RESULT_DIR):
            p = os.path.join(RESULT_DIR, f)
            if f.endswith(".xlsx") and os.path.getmtime(p) < limit:
                os.remove(p)


def main():
    c = db().cursor()
    c.execute("UPDATE blast_job SET status='queued', gestartet=NULL WHERE status='running'")  # after a crash
    last_clean = last_ram = last_house = 0
    while True:
        try:
            c.connection.ping(reconnect=True)
            if time.time() - last_ram > 30:
                write_ram_status(c)
                last_ram = time.time()
            if time.time() - last_clean > 600:
                c.execute("DELETE FROM blast_api_hits WHERE minute < %s", (int(time.time() // 60) - 10,))
                last_clean = time.time()
            if time.time() - last_house > 10:
                housekeeping(c)
                last_house = time.time()
            c.execute("SELECT dmnd_hash FROM blast_release WHERE quelle='nr' AND aktiv=1 ORDER BY nr_datum DESC LIMIT 1")
            row = c.fetchone()
            if not row or row[0] is None:
                time.sleep(60)
                continue
            # one job at a time; ad-hoc jobs (query form, short by construction) before waiting key/full jobs
            c.execute("SELECT job_id, mode, sensitivity, evalue, max_target_seqs, taxonlist, query, "
                      "len_min, len_max, new_days, query_letters, tier, budget_s, db FROM blast_job WHERE status='queued' "
                      "ORDER BY tier = 'adhoc' DESC, erstellt LIMIT 1")
            job = c.fetchone()
            if not job:
                time.sleep(2)
                continue
            c.execute("UPDATE blast_job SET status='running', gestartet=NOW() WHERE job_id=%s AND status='queued'", (job[0],))
            log(f"{job[0]}: {job[11]} {job[1]} {job[2]} taxonlist={job[5]}")
            t_job = time.time()
            try:
                run_job(c, job)
                if job[11] in ("adhoc", "full"):
                    write_xlsx(c, job[0])
            except Rejected as e:
                c.connection.ping(reconnect=True)
                c.execute("UPDATE blast_job SET status='rejected', fertig=NOW(), sekunden=%s, error=%s WHERE job_id=%s",
                          (round(time.time() - t_job, 1), str(e)[:2000], job[0]))
                log(f"{job[0]}: rejected: {e}")
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
