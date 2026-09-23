#!/usr/bin/env python3
"""NCBI-nr-Metadaten (OID, Accessions, TaxIDs, Laenge, Titel) nach wagodb, Tabellen blast_*.

Resumable je Chunk ueber blast_import: ein Neustart ueberspringt bereits geladene OIDs.
Liest aus dem Hardlink-Abbild ~/blastdb_hold, damit das Loeschen der Original-BLAST-
Dateien durch die DIAMOND-Pipeline den Import nicht beeinflusst.
"""
import os, sys, time, queue, threading, subprocess, shutil

import blast_db, pymysql

BLASTDBCMD = os.path.expanduser("~/iver_sim/mamba/envs/blast/bin/blastdbcmd")
HOLD = os.path.expanduser("~/blastdb_hold")
TMP = os.path.expanduser("~/blast_meta_tmp")
TAXDIR = "/mnt/archive/taxdump"
TEST_LINES = int(os.environ.get("BLAST_META_TEST", "0"))
CHUNK_SEQS = 5_000_000 if not TEST_LINES else 1000
LOG = os.path.expanduser("~/python/blast_meta2db.log")


def log(msg):
    line = time.strftime("[%F %T] ") + msg
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def conn():
    return pymysql.connect(**blast_db.cfg(), autocommit=True, local_infile=True)


def run_sql_file(c, path):
    stmts = [s.strip() for s in open(path).read().split(";") if s.strip()]
    for s in stmts:
        c.execute(s)


def nr_sequence_count():
    out = subprocess.run([BLASTDBCMD, "-db", "nr", "-info"], capture_output=True, text=True,
                         env={**os.environ, "BLASTDB": HOLD}).stdout
    for line in out.splitlines():
        if "sequences;" in line:
            return int(line.split("sequences;")[0].strip().replace(",", ""))
    raise RuntimeError("Sequenzzahl aus blastdbcmd -info nicht lesbar")


def clean(s):
    return s.replace("\t", " ").replace("\r", " ").replace("\n", " ")


def producer(q, start_oid, chunk_no):
    env = {**os.environ, "BLASTDB": HOLD}
    p = subprocess.Popen([BLASTDBCMD, "-db", "nr", "-entry", "all",
                          "-outfmt", "%o\t%a\t%T\t%l\t%t"],
                         stdout=subprocess.PIPE, env=env, bufsize=16 * 1024 * 1024)
    cur_oid, pos = -1, 0
    seq_f = acc_f = None
    first_oid, n_seq, n_acc = None, 0, 0
    pending_seq = None

    def open_chunk():
        nonlocal seq_f, acc_f, first_oid, n_seq, n_acc
        seq_f = open(f"{TMP}/seq_{chunk_no}.tsv", "w", encoding="utf-8")
        acc_f = open(f"{TMP}/acc_{chunk_no}.tsv", "w", encoding="ascii", errors="replace")
        first_oid, n_seq, n_acc = None, 0, 0

    def close_chunk(last_oid):
        nonlocal chunk_no
        seq_f.close(); acc_f.close()
        q.put((chunk_no, first_oid, last_oid, n_seq, n_acc))
        chunk_no += 1

    def flush_seq():
        nonlocal n_seq, first_oid
        o, ln, npos, title = pending_seq
        seq_f.write(f"{o}\t{ln}\t{npos}\t{title}\n")
        n_seq += 1
        if first_oid is None:
            first_oid = o

    open_chunk()
    n_lines = 0
    for raw in p.stdout:
        n_lines += 1
        if TEST_LINES and n_lines > TEST_LINES:
            p.kill()
            break
        parts = raw.decode("utf-8", errors="replace").rstrip("\n").split("\t", 4)
        if len(parts) < 5:
            parts += [""] * (5 - len(parts))
        oid = int(parts[0])
        if oid <= start_oid:
            continue
        if oid != cur_oid:
            if oid < cur_oid:
                raise RuntimeError(f"OID-Reihenfolge verletzt: {oid} nach {cur_oid}")
            if pending_seq is not None:
                pending_seq = (pending_seq[0], pending_seq[1], pos, pending_seq[3])
                flush_seq()
                if n_seq >= CHUNK_SEQS:
                    close_chunk(cur_oid)
                    open_chunk()
            cur_oid, pos = oid, 0
            pending_seq = (oid, int(parts[3] or 0), 0, clean(parts[4]))
        taxid = int(parts[2]) if parts[2].strip().isdigit() else 0
        acc_f.write(f"{oid}\t{pos}\t{clean(parts[1])}\t{taxid}\n")
        pos += 1
        n_acc += 1
    rc = p.wait()
    if TEST_LINES:
        rc = 0
    if pending_seq is not None:
        pending_seq = (pending_seq[0], pending_seq[1], pos, pending_seq[3])
        flush_seq()
        close_chunk(cur_oid)
    else:
        seq_f.close(); acc_f.close()
    q.put(("END", rc))


def load_chunk(c, item):
    chunk_no, first_oid, last_oid, n_seq, n_acc = item
    t0 = time.time()
    sf, af = f"{TMP}/seq_{chunk_no}.tsv", f"{TMP}/acc_{chunk_no}.tsv"
    c.execute(f"LOAD DATA LOCAL INFILE '{sf}' INTO TABLE blast_seq CHARACTER SET utf8mb4 "
              "FIELDS TERMINATED BY '\\t' ESCAPED BY '' LINES TERMINATED BY '\\n' "
              "(oid, len, n_acc, title)")
    c.execute(f"LOAD DATA LOCAL INFILE '{af}' INTO TABLE blast_acc CHARACTER SET ascii "
              "FIELDS TERMINATED BY '\\t' ESCAPED BY '' LINES TERMINATED BY '\\n' "
              "(oid, pos, acc, taxid)")
    dt = time.time() - t0
    c.execute("INSERT INTO blast_import (chunk_no, first_oid, last_oid, n_seq, n_acc, sekunden) "
              "VALUES (%s,%s,%s,%s,%s,%s)", (chunk_no, first_oid, last_oid, n_seq, n_acc, round(dt, 2)))
    os.remove(sf); os.remove(af)
    log(f"Chunk {chunk_no}: OID {first_oid}-{last_oid}, {n_seq} Seq, {n_acc} Acc, {dt:.0f}s")


def load_taxonomy(c):
    names = {}
    with open(f"{TAXDIR}/names.dmp", encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\t|\n").split("\t|\t")
            if len(p) >= 4 and p[3] == "scientific name":
                names[p[0]] = p[1]
    path = f"{TMP}/taxon.tsv"
    with open(f"{TAXDIR}/nodes.dmp", encoding="utf-8") as f, open(path, "w", encoding="utf-8") as o:
        for line in f:
            p = line.rstrip("\t|\n").split("\t|\t")
            o.write(f"{p[0]}\t{p[1]}\t{p[2]}\t{clean(names.get(p[0], ''))}\n")
    c.execute("TRUNCATE blast_taxon")
    c.execute(f"LOAD DATA LOCAL INFILE '{path}' INTO TABLE blast_taxon CHARACTER SET utf8mb4 "
              "FIELDS TERMINATED BY '\\t' ESCAPED BY '' LINES TERMINATED BY '\\n' "
              "(taxid, parent, `rank`, name)")
    os.remove(path)
    c.execute("SELECT COUNT(*) FROM blast_taxon")
    log(f"blast_taxon geladen: {c.fetchone()[0]} Taxa")


def main():
    os.makedirs(TMP, exist_ok=True)
    c = conn().cursor()
    run_sql_file(c, os.path.expanduser("~/python/blast_schema.sql"))
    c.execute("SET SESSION unique_checks=0, foreign_key_checks=0")

    c.execute("SELECT COALESCE(MAX(last_oid), -1), COALESCE(MAX(chunk_no), -1) FROM blast_import")
    last_oid, last_chunk = c.fetchone()
    last_oid, last_chunk = int(last_oid), int(last_chunk)
    c.execute("DELETE FROM blast_seq WHERE oid > %s", (last_oid,))
    c.execute("DELETE FROM blast_acc WHERE oid > %s", (last_oid,))
    for fn in os.listdir(TMP):
        os.remove(os.path.join(TMP, fn))
    log(f"=== Start: fortsetzen nach OID {last_oid} (Chunk {last_chunk + 1}) ===")

    q = queue.Queue(maxsize=2)
    err = []

    def prod():
        try:
            producer(q, last_oid, last_chunk + 1)
        except Exception as e:
            err.append(e)
            q.put(("END", -1))

    th = threading.Thread(target=prod, daemon=True)
    th.start()
    while True:
        item = q.get()
        if item[0] == "END":
            if err:
                raise err[0]
            if item[1] != 0:
                raise RuntimeError(f"blastdbcmd exit={item[1]}")
            break
        load_chunk(c, item)
    th.join()

    if TEST_LINES:
        load_taxonomy(c)
        log("=== TEST FERTIG (keine Verifikation, keine Indizes, blastdb_hold bleibt) ===")
        return

    log("=== Verifikation ===")
    expected = nr_sequence_count()
    c.execute("SELECT COUNT(*), MIN(oid), MAX(oid) FROM blast_seq")
    n, mn, mx = c.fetchone()
    c.execute("SELECT SUM(n_acc) FROM blast_import")
    n_acc = int(c.fetchone()[0])
    c.execute("SELECT COUNT(*) FROM blast_acc")
    n_acc_db = c.fetchone()[0]
    log(f"nr Sequenzen={expected} blast_seq={n} (OID {mn}..{mx}) blast_acc={n_acc_db} (erwartet {n_acc})")
    if n != expected or mn != 0 or mx != expected - 1 or n_acc_db != n_acc:
        log("FEHLER: Verifikation fehlgeschlagen, blastdb_hold bleibt erhalten")
        sys.exit(1)

    log("=== Indizes auf blast_acc (acc, taxid) ===")
    t0 = time.time()
    c.execute("ALTER TABLE blast_acc ADD INDEX idx_acc (acc), ADD INDEX idx_taxid (taxid)")
    log(f"Indizes fertig in {time.time() - t0:.0f}s")

    load_taxonomy(c)

    shutil.rmtree(HOLD)
    log("blastdb_hold entfernt (Hardlink-Abbild nicht mehr noetig)")
    c.execute("SELECT table_name, ROUND((data_length+index_length)/1024/1024/1024,1) "
              "FROM information_schema.tables WHERE table_schema='wagodb' AND table_name LIKE 'blast\\_%'")
    for t, gb in c.fetchall():
        log(f"  {t}: {gb} GB")
    log("=== BLAST-META FERTIG ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FEHLER: {type(e).__name__}: {e}")
        sys.exit(1)
