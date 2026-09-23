#!/usr/bin/env python3
"""NCBI nr in EINEM Lesedurchgang: nr_full.dmnd (DIAMOND) und Metadaten in wagodb (blast_*).

Ein blastdbcmd-Strom (eine Zeile je Defline: OID, Accession, TaxID, Laenge, Sequenz, Titel) wird verteilt:
  - an diamond makedb (stdin): je Sequenz eine FASTA-Kopfzeile nur aus den Accessions (">acc1 >acc2 ...").
    Titel stehen damit nur in MariaDB; die Taxon-Listen bleiben vollstaendig. Leersequenzen (Laenge 0)
    lehnt makedb ab: sie werden ausgelassen und in blast_dmnd_luecke vermerkt (OID -> dmnd-Satznummer).
  - an MariaDB: Chunk-Dateien je 5 Mio. Sequenzen, geladen von einem eigenen Thread mit
    blast_meta2db.load_chunk (LOAD DATA). Die Warteschlange ist begrenzt: ist MariaDB langsamer,
    wartet der Strom, statt die Platte zu fuellen.
Danach: Verifikation beider Ziele gegen blastdbcmd -info, Indizes auf blast_acc (innodb_tmpdir auf der
NVMe), Taxonomie, Luecken, blast_release (dmnd_hash, dmnd_sequenzen, meta_importiert).
Die BLAST-nr wird NICHT geloescht (erst nach dmnd_getseq-Abgleich und Freigabe, Doku Abschnitt 9).

    python3 ~/python/nr_build.py                   voller Bau (nicht fortsetzbar: makedb ist Single-Pass)
    NR_BUILD_TEST=20000 python3 ~/python/nr_build.py   Probe mit den ersten 20.000 OIDs, Test-dmnd
"""
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

import blast_meta2db as m

TEST = int(os.environ.get("NR_BUILD_TEST", "0"))
DIAMOND = os.path.expanduser("~/src/diamond/build/diamond")      # v2.2.8 + --append + Offset-Tabelle auf Platte
DB = "/mnt/archive/test_nrbuild/nr_test" if TEST else "/mnt/archive/nr_full"
TMPDIR_DMND = os.path.dirname(DB)
CHUNK_SEQS = 5_000_000 if not TEST else 5_000
MAX_CHUNKS_WARTEND = 6          # ~7 GB Chunk-Dateien auf der NVMe, dann bremst der Strom
INNODB_TMPDIR = "/var/lib/mariadb-tmp"
LOG = os.path.expanduser("~/python/nr_build.log")
m.LOG = LOG                     # load_chunk/load_taxonomy protokollieren ebenfalls hierhin
m.CHUNK_SEQS = CHUNK_SEQS
log = m.log

LUECKE_SQL = ("CREATE TABLE IF NOT EXISTS blast_dmnd_luecke ("
              " oid INT UNSIGNED NOT NULL PRIMARY KEY"
              ") ENGINE=InnoDB COMMENT='Leersequenzen der nr, nicht in nr_full.dmnd: "
              "dmnd-Satznummer = oid - (Zahl der Luecken mit kleinerer oid)'")


def makedb_starten():
    os.makedirs(TMPDIR_DMND, exist_ok=True)
    for alt in (DB + ".dmnd", DB + ".pos_tmp"):
        if os.path.exists(alt):
            log(f"entferne {alt} ({os.path.getsize(alt) / 1e9:.1f} GB)")
            os.remove(alt)
    cmd = [DIAMOND, "makedb", "--db", DB,
           "--taxonmap", f"{m.TAXDIR}/prot.accession2taxid.FULL.gz",
           "--taxonnodes", f"{m.TAXDIR}/nodes.dmp", "--taxonnames", f"{m.TAXDIR}/names.dmp",
           "--tmpdir", TMPDIR_DMND, "--threads", "20"]
    log("makedb: " + " ".join(cmd))
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=open(DB + ".makedb.log", "w"),
                            stderr=subprocess.STDOUT, bufsize=16 * 1024 * 1024)


def strom(q, fasta, luecken, zaehler):
    """blastdbcmd lesen, FASTA an makedb, Chunk-Dateien an den Lade-Thread."""
    p = subprocess.Popen([m.BLASTDBCMD, "-db", "nr", "-entry", "all", "-outfmt", "%o\t%a\t%T\t%l\t%s\t%t"],
                         stdout=subprocess.PIPE, env={**os.environ, "BLASTDB": m.HOLD}, bufsize=16 * 1024 * 1024)
    chunk_no, seq_f, acc_f = 0, None, None
    first_oid = n_seq = n_acc = 0

    def chunk_auf():
        nonlocal seq_f, acc_f, first_oid, n_seq, n_acc
        seq_f = open(f"{m.TMP}/seq_{chunk_no}.tsv", "wb")
        acc_f = open(f"{m.TMP}/acc_{chunk_no}.tsv", "wb")
        first_oid, n_seq, n_acc = None, 0, 0

    def chunk_zu(last_oid):
        nonlocal chunk_no
        seq_f.close(); acc_f.close()
        q.put((chunk_no, first_oid, last_oid, n_seq, n_acc))       # blockiert bei voller Warteschlange
        chunk_no += 1

    cur, accs, ln, seq, title = -1, [], b"", b"", b""
    letzte = -1

    def sequenz_fertig():
        nonlocal n_seq, first_oid, letzte
        letzte = cur
        seq_f.write(b"%d\t%s\t%d\t%s\n" % (cur, ln or b"0", len(accs), title))
        n_seq += 1
        if first_oid is None:
            first_oid = cur
        if seq:
            fasta.write(b">" + b" >".join(accs) + b"\n" + seq + b"\n")
        else:
            luecken.append(cur)
        zaehler["seq"] += 1

    chunk_auf()
    t0, n_zeilen = time.time(), 0
    for raw in p.stdout:
        n_zeilen += 1
        teile = raw.rstrip(b"\n").split(b"\t", 5)
        if len(teile) < 6:
            teile += [b""] * (6 - len(teile))
        oid = int(teile[0])
        if oid != cur:
            if cur >= 0:
                if oid < cur:
                    raise RuntimeError(f"OID-Reihenfolge verletzt: {oid} nach {cur}")
                sequenz_fertig()
                if n_seq >= CHUNK_SEQS:
                    chunk_zu(cur)
                    chunk_auf()
                if TEST and zaehler["seq"] >= TEST:
                    cur = -1
                    p.kill()
                    break
            cur, accs, ln, seq = oid, [], teile[3], teile[4]
            title = m.clean(teile[5].decode("utf-8", "replace")).encode("utf-8")
        acc = teile[1] if teile[1].isascii() else teile[1].decode("ascii", "replace").encode("ascii", "replace")
        acc = acc.replace(b"\t", b" ")
        taxid = teile[2] if teile[2].isdigit() else b"0"
        acc_f.write(b"%d\t%d\t%s\t%s\n" % (oid, len(accs), acc, taxid))
        accs.append(acc)
        n_acc += 1
        zaehler["acc"] += 1
        if n_zeilen % 50_000_000 == 0:
            dt = time.time() - t0
            log(f"Strom: {zaehler['seq']:,} Sequenzen, {zaehler['acc']:,} Deflines, "
                f"{n_zeilen / dt:,.0f} Zeilen/s, Leersequenzen {len(luecken)}")
    rc = p.wait()
    if cur >= 0:
        sequenz_fertig()
    if n_seq:
        chunk_zu(letzte)
    else:                                   # Chunk ging genau auf: leere Dateien nicht laden
        seq_f.close(); acc_f.close()
        os.remove(seq_f.name); os.remove(acc_f.name)
    fasta.close()
    if TEST:
        rc = 0
    return rc


def main():
    os.makedirs(m.TMP, exist_ok=True)
    for fn in os.listdir(m.TMP):
        os.remove(os.path.join(m.TMP, fn))
    log(f"=== nr_build: Start{' (TEST ' + str(TEST) + ' Sequenzen)' if TEST else ''} ===")

    c = m.conn().cursor()
    m.run_sql_file(c, os.path.expanduser("~/python/blast_schema.sql"))
    c.execute(LUECKE_SQL)
    for t in ("blast_seq", "blast_acc", "blast_import", "blast_taxon", "blast_dmnd_luecke"):
        c.execute(f"TRUNCATE {t}")
    c.execute("SELECT index_name FROM information_schema.statistics WHERE table_schema = DATABASE() "
              "AND table_name = 'blast_acc' AND index_name IN ('idx_acc', 'idx_taxid') GROUP BY 1")
    for (idx,) in c.fetchall():
        c.execute(f"ALTER TABLE blast_acc DROP INDEX {idx}")      # Indizes erst nach dem Laden bauen
    c.execute("UPDATE blast_release SET dmnd_hash = NULL, dmnd_sequenzen = NULL, meta_importiert = NULL "
              "WHERE release_id = 1")
    c.execute("SET SESSION unique_checks=0, foreign_key_checks=0")

    q = queue.Queue(maxsize=MAX_CHUNKS_WARTEND)
    fehler = []

    def laden():
        try:
            while True:
                item = q.get()
                if item is None:
                    return
                m.load_chunk(c, item)
        except Exception as e:
            fehler.append(e)
            while q.get() is not None:                 # Strom nicht blockieren lassen
                pass

    lader = threading.Thread(target=laden)
    lader.start()

    mk = makedb_starten()
    luecken, zaehler = [], {"seq": 0, "acc": 0}
    t0 = time.time()
    try:
        rc_blast = strom(q, mk.stdin, luecken, zaehler)
    finally:
        q.put(None)
        lader.join()
    rc_mk = mk.wait()
    log(f"Strom fertig: {zaehler['seq']:,} Sequenzen, {zaehler['acc']:,} Deflines, {len(luecken)} Leersequenzen, "
        f"blastdbcmd exit {rc_blast}, makedb exit {rc_mk}, {time.time() - t0:.0f}s")
    if fehler:
        raise fehler[0]
    if rc_blast != 0 or rc_mk != 0 or not os.path.exists(DB + ".dmnd"):
        log(f"FEHLER: Bau fehlgeschlagen (makedb-Ausgabe: {DB}.makedb.log). BLAST-nr unveraendert.")
        sys.exit(1)

    log("=== Verifikation ===")
    info = subprocess.run([DIAMOND, "dbinfo", "--db", DB + ".dmnd"], capture_output=True, text=True).stdout
    n_dmnd = int(re.search(r"Sequences\s+(\d+)", info).group(1))
    mk_log = open(DB + ".makedb.log", errors="replace").read()
    h = re.search(r"Database hash\s*=?\s*([0-9a-f]{32})", mk_log)
    dmnd_hash = h.group(1) if h else None
    erwartet = TEST or m.nr_sequence_count()
    c = m.conn().cursor()
    c.execute("SELECT COUNT(*), MIN(oid), MAX(oid) FROM blast_seq")
    n, mn, mx = c.fetchone()
    c.execute("SELECT SUM(n_acc) FROM blast_import")
    n_acc = int(c.fetchone()[0])
    c.execute("SELECT COUNT(*) FROM blast_acc")
    n_acc_db = c.fetchone()[0]
    log(f"nr={erwartet} blast_seq={n} (OID {mn}..{mx}) blast_acc={n_acc_db} (erwartet {n_acc}) "
        f"dmnd={n_dmnd} + Leersequenzen {len(luecken)} = {n_dmnd + len(luecken)}, hash {dmnd_hash}")
    if (n != erwartet or mn != 0 or mx != erwartet - 1 or n_acc_db != n_acc or n_acc != zaehler["acc"]
            or n_dmnd + len(luecken) != erwartet or not dmnd_hash):
        log("FEHLER: Verifikation fehlgeschlagen")
        sys.exit(1)

    if luecken:
        c.executemany("INSERT INTO blast_dmnd_luecke (oid) VALUES (%s)", [(o,) for o in luecken])
    c.execute("SELECT @@session.innodb_tmpdir")
    tmp = c.fetchone()[0]
    if tmp != INNODB_TMPDIR:
        log(f"FEHLER: innodb_tmpdir ist {tmp!r} statt {INNODB_TMPDIR} (tmpfs /tmp liefe voll) – keine Indizes")
        sys.exit(1)
    log(f"=== Indizes auf blast_acc (acc, taxid), innodb_tmpdir {tmp} ===")
    t1 = time.time()
    c.execute("ALTER TABLE blast_acc ADD INDEX idx_acc (acc), ADD INDEX idx_taxid (taxid)")
    log(f"Indizes fertig in {time.time() - t1:.0f}s")
    m.load_taxonomy(c)

    if not TEST:
        c.execute("UPDATE blast_release SET dmnd_datei = %s, dmnd_hash = %s, dmnd_sequenzen = %s, "
                  "meta_importiert = NOW() WHERE release_id = 1", (DB + ".dmnd", dmnd_hash, n_dmnd))
    c.execute("SELECT table_name, ROUND((data_length+index_length)/1e9,1) FROM information_schema.tables "
              "WHERE table_schema='wagodb' AND table_name LIKE 'blast\\_%'")
    for t, gb in c.fetchall():
        log(f"  {t}: {gb} GB")
    log(f"  {DB}.dmnd: {os.path.getsize(DB + '.dmnd') / 1e9:.1f} GB")
    log(f"=== NR-BUILD FERTIG{' (TEST)' if TEST else ''} in {(time.time() - t0) / 3600:.1f} h ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FEHLER: {type(e).__name__}: {e}")
        sys.exit(1)
