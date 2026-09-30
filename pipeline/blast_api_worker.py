#!/usr/bin/env python3
"""Fuehrt die DIAMOND-Suchauftraege der API (blast_job) nacheinander aus.
Haelt waehrend der Suche eine geteilte Sperre auf /home/gh/.nr_dmnd.lock, damit
diamond_append.py / nr_daily_delta.py die .dmnd nicht mitten in einer Suche veraendern.

RAM-Planung: vor jeder Suche wird der Bedarf geschaetzt als SOCKEL + k * min(Reste der DB, b) (in Mrd. Resten)
und --block-size b so gewaehlt, dass er unter min(RAM_MAX_GB, MemAvailable - RAM_RESERVE_GB) bleibt.
k (GB je Mrd. Reste) kommt aus blast_ram_mess: gemessene Spitzen-RSS frueherer Suchen gleicher Sensitivitaet
und aehnlicher Anfragegroesse; ohne Messung gelten die Startwerte K_START. Jede Suche laeuft unter
/usr/bin/time und traegt ihr Spitzen-RSS wieder in blast_ram_mess ein."""
import os, sys, time, fcntl, tempfile, subprocess
import blast_db, pymysql
import dmnd_vorauswahl
from dmnd_getseq import Dmnd

E = os.environ.get
DMND = E("DMND", "/home/gh/diamond/nr_full.dmnd")
DIAMOND = E("DIAMOND_BIN", os.path.expanduser("~/.local/share/mamba/envs/diamond/bin/diamond"))
LOCK = E("NR_LOCK", "/home/gh/.nr_dmnd.lock")
TMP = E("JOB_TMP", "/mnt/archive/blast_api_tmp")
TIMEOUT = int(E("JOB_TIMEOUT", "3600"))
THREADS = E("JOB_THREADS", "20")
RAM_MAX_GB = float(E("RAM_MAX_GB", "48"))          # Obergrenze fuer eine Suche (Vorgabe des Betreibers)
RAM_RESERVE_GB = float(E("RAM_RESERVE_GB", "4"))   # bleibt fuer MariaDB & Co. immer frei
RAM_WARTEN_MIN = float(E("RAM_WARTEN_MIN", "30"))  # so lange auf freien Speicher warten, dann Fehler
SOCKEL_GB = 0.5                                    # diamond ohne Block (Code, Puffer, Anfrage)
BLOCKS = (2.0, 1.0, 0.5, 0.25, 0.1)                # --block-size-Stufen, groesste zuerst (weniger Bloecke = schneller)
# GB je Mrd. Reste im Block, bevor es Messungen gibt. fast/sensitive aus Messungen vom 24./30.09.2026 (kleine
# Anfrage, ~1,3 bzw. ~1,8); die uebrigen sind vorsichtige Annahmen, bis die Messtabelle sie ersetzt.
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


def ram_status_schreiben(c):
    """Fuer die API (Apache sieht /proc/meminfo wegen ProcSubset=pid nicht): eine Zeile in blast_ram_status."""
    m = {z.split(":")[0]: int(z.split()[1]) / 1048576 for z in open("/proc/meminfo") if z.split()[1:2]}
    c.execute("REPLACE INTO blast_ram_status (id, frei_gb, gesamt_gb, swap_frei_gb, stand) VALUES (1,%s,%s,%s,NOW())",
              (round(m.get("MemAvailable", 0), 2), round(m.get("MemTotal", 0), 2), round(m.get("SwapFree", 0), 2)))


def k_schaetzen(c, mode, sens, q_letters):
    """GB je Mrd. Reste: groesster gemessener Wert bei gleichem Modus und gleicher Sensitivitaet und Anfrage
    im Bereich q/4..4q (blastx: Nukleotide, daher eigener Modus),
    sonst aus groesseren Anfragen, sonst Startwert (bei grossen Anfragen verdoppelt). Plus 20 % Sicherheit."""
    for sql, args in (("query_letters BETWEEN %s AND %s", (q_letters / 4, q_letters * 4)),
                      ("query_letters >= %s", (q_letters,))):
        c.execute("SELECT MAX((ram_gb - %s) / eff_gletters), COUNT(*) FROM blast_ram_mess "
                  f"WHERE ok = 1 AND mode = %s AND sensitivity = %s AND eff_gletters >= 0.05 AND {sql}",
                  (SOCKEL_GB, mode, sens, *args))
        k, n = c.fetchone()
        if n:
            return max(float(k), 0.1) * 1.2, f"{n} Messungen"
    k = K_START.get(sens, 12.0) * (2 if q_letters > 5000 else 1) * (2 if mode == "blastx" else 1)
    return k * 1.2, "Startwert"


def ram_plan(c, mode, sens, q_letters, db_letters):
    """-> (block_size, schaetzung_gb, grenze_gb, frei_gb, herkunft_k). Wartet, wenn nichts passt."""
    k, herkunft = k_schaetzen(c, mode, sens, q_letters)
    t0 = time.time()
    while True:
        frei = mem_available_gb()
        grenze = min(RAM_MAX_GB, frei - RAM_RESERVE_GB)
        for b in BLOCKS:
            bedarf = SOCKEL_GB + k * min(db_letters / 1e9, b)
            if bedarf <= grenze:
                return b, round(bedarf, 2), round(grenze, 2), round(frei, 2), herkunft
            if db_letters / 1e9 <= b / 2:          # DB passt schon in den naechstkleineren Block: kleiner hilft nicht
                break
        if time.time() - t0 > RAM_WARTEN_MIN * 60:
            raise RuntimeError(f"nicht genug RAM: Bedarf {bedarf:.1f} GB bei --block-size {b}, "
                               f"frei {frei:.1f} GB, Grenze {grenze:.1f} GB")
        log(f"RAM knapp (frei {frei:.1f} GB, Bedarf ab {bedarf:.1f} GB), warte")
        time.sleep(60)


def staxids_nachtragen(c, rows):
    """Die Teil-.dmnd hat keine Taxonomie: Taxa je Treffer aus blast_acc (alle Accessions derselben OID)."""
    accs = sorted({r[3] for r in rows})
    tax = {}
    for i in range(0, len(accs), 500):
        teil = accs[i:i + 500]
        c.execute("SELECT x.acc, GROUP_CONCAT(DISTINCT y.taxid ORDER BY y.taxid SEPARATOR ';') FROM blast_acc x "
                  "JOIN blast_acc y ON y.oid = x.oid WHERE x.acc IN (" + ",".join(["%s"] * len(teil)) + ") "
                  "GROUP BY x.acc", teil)
        tax.update(c.fetchall())
    return [(*r[:14], tax.get(r[3], "")) for r in rows]


def nachfiltern(c, rows, len_min, len_max, neu_tage):
    """Nur bei Vollsuche als Rueckfall: Laenge und Anlagedatum nach der Suche pruefen."""
    if not (len_min or len_max or neu_tage):
        return rows
    ok = set()
    for i in range(0, len(rows), 500):
        teil = sorted({r[3] for r in rows[i:i + 500]})
        c.execute("SELECT a.acc, s.len, n.createdate >= CURDATE() - INTERVAL %s DAY FROM blast_acc a "
                  "JOIN blast_seq s ON s.oid = a.oid LEFT JOIN blast_seq_ncbi n ON n.oid = a.oid "
                  "WHERE a.acc IN (" + ",".join(["%s"] * len(teil)) + ")", [int(neu_tage or 0), *teil])
        for acc, ln, neu in c.fetchall():
            if (not len_min or ln >= len_min) and (not len_max or ln <= len_max) and (not neu_tage or neu):
                ok.add(acc)
    return [(r[0], n, *r[2:]) for n, r in enumerate(x for x in rows if x[3] in ok)]


def run_job(c, job):
    job_id, mode, sens, evalue, max_t, taxonlist, query, len_min, len_max, neu_tage, q_letters = job
    os.makedirs(TMP, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TMP) as d:
        q, o = os.path.join(d, "q.fa"), os.path.join(d, "out.tsv")
        open(q, "w").write(query)
        cmd = [DIAMOND, mode, "--query", q, "--out", o, "--evalue", str(evalue),
               "--max-target-seqs", str(max_t), f"--{sens}", "--threads", THREADS, "--tmpdir", d,
               "--outfmt", "6", *FIELDS]
        t0 = time.time()
        vw, nachfilter, db_letters = None, False, None
        with open(LOCK, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)           # wartet, falls gerade ein Append laeuft
            c.execute("SELECT release_id, dmnd_hash FROM blast_release WHERE quelle='nr' AND aktiv=1 "
                      "ORDER BY nr_datum DESC LIMIT 1")
            rel, dhash = c.fetchone()
            if taxonlist or len_min or len_max or neu_tage:
                taxa = [int(x) for x in (taxonlist or "").split(",") if x]
                try:
                    m = dmnd_vorauswahl.vorauswahl(c, taxa, len_min, len_max, neu_tage, threads=THREADS)
                    cmd.remove("staxids")                  # Teil-.dmnd ohne Taxonomie, Taxa kommen aus blast_acc
                    cmd += ["--db", m["dmnd"], "--dbsize", str(m["dbsize"])]   # E-Werte wie bei der Vollsuche
                    vw = (f"{m['n_seq']} Sequenzen, {m['letters']} Reste, {m['sekunden']} s"
                          + (" (Cache)" if m["cache"] else ""))
                    db_letters = m["letters"]
                except dmnd_vorauswahl.ZuGross as e:
                    cmd += ["--db", DMND] + (["--taxonlist", taxonlist] if taxonlist else [])
                    vw, nachfilter = f"Vollsuche, Vorauswahl {e}", True
            else:
                cmd += ["--db", DMND]
            if db_letters is None:
                db_letters = Dmnd(DMND).letters
            b, schaetz, grenze, frei, herkunft = ram_plan(c, mode, sens, q_letters, db_letters)
            log(f"{job_id}: RAM frei {frei} GB, Grenze {grenze} GB, Schaetzung {schaetz} GB ({herkunft}), "
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
            c.execute("INSERT INTO blast_ram_mess (job_id, mode, sensitivity, query_letters, db_letters, block_size, "
                      "eff_gletters, threads, ram_gb, schaetz_gb, frei_gb, sekunden, ok) "
                      "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (job_id, mode, sens, q_letters, db_letters, b, min(db_letters / 1e9, b), THREADS, ram,
                       schaetz, frei, round(time.time() - t_diamond, 1), int(r.returncode == 0)))
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout)[-1000:])
        rows = []
        for n, line in enumerate(open(o)):
            p = line.rstrip("\n").split("\t")
            rows.append((job_id, n, *p[:12], p[12] if len(p) > 12 else ""))
    if vw and not nachfilter:
        rows = staxids_nachtragen(c, rows)
    if nachfilter:
        rows = nachfiltern(c, rows, len_min, len_max, neu_tage)
    c.connection.begin()
    c.execute("DELETE FROM blast_job_hit WHERE job_id=%s", (job_id,))
    if rows:
        c.executemany("INSERT INTO blast_job_hit (job_id, n, qseqid, sseqid, pident, length, mismatch, gapopen, "
                      "qstart, qend, sstart, send, evalue, bitscore, staxids) "
                      "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
    c.execute("UPDATE blast_job SET status='done', fertig=NOW(), sekunden=%s, release_id=%s, dmnd_hash=%s, "
              "n_hits=%s, vorauswahl=%s, block_size=%s, ram_schaetz_gb=%s, ram_gb=%s, ram_frei_gb=%s WHERE job_id=%s",
              (secs, rel, dhash, len(rows), vw, b, schaetz, ram, frei, job_id))
    c.connection.commit()
    log(f"{job_id}: {len(rows)} Treffer in {secs}s")


def main():
    c = db().cursor()
    c.execute("UPDATE blast_job SET status='queued', gestartet=NULL WHERE status='running'")  # nach Absturz
    last_clean = last_ram = 0
    while True:
        try:
            c.connection.ping(reconnect=True)
            if time.time() - last_ram > 30:
                ram_status_schreiben(c)
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
                      "len_min, len_max, neu_tage, query_letters FROM blast_job WHERE status='queued' ORDER BY erstellt LIMIT 1")
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
                log(f"{job[0]}: FEHLER {type(e).__name__}: {e}")
        except pymysql.MySQLError as e:
            log(f"DB-Fehler: {e}")
            time.sleep(30)


if __name__ == "__main__":
    main()
