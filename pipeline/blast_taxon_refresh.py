#!/usr/bin/env python3
"""Reload wagodb.blast_taxon from the NCBI taxdump without a gap for the API.

nr_daily_delta.py refreshes nodes.dmp/names.dmp every 30 days but only for diamond; new taxids then had no
name/lineage in blast_taxon. This loads into blast_taxon_new and swaps atomically (RENAME TABLE).
Runs only when nodes.dmp is newer than the last load (marker file), so a daily cron is cheap.

cron: 55 4 * * *  python3 $HOME/python/blast_taxon_refresh.py >> $HOME/python/blast_taxon_refresh.log 2>&1
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blast_db  # noqa: E402
import pymysql  # noqa: E402

TAXDIR = os.environ.get("TAXDIR", "/mnt/archive/taxdump")          # same as nr_daily_delta.py
TMP = os.path.expanduser(os.environ.get("BLAST_TMP", "~/blast_meta_tmp"))
MARKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".blast_taxon_loaded")


def clean(s):
    return s.replace("\t", " ").replace("\r", " ").replace("\n", " ")


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def main():
    nodes = f"{TAXDIR}/nodes.dmp"
    if "--force" not in sys.argv and os.path.exists(MARKER) and os.path.getmtime(MARKER) >= os.path.getmtime(nodes):
        log("blast_taxon is up to date with nodes.dmp - nothing to do")
        return
    t0 = time.time()
    names = {}
    with open(f"{TAXDIR}/names.dmp", encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\t|\n").split("\t|\t")
            if len(p) >= 4 and p[3] == "scientific name":
                names[p[0]] = p[1]
    os.makedirs(TMP, exist_ok=True)
    path = f"{TMP}/taxon_refresh.tsv"
    with open(nodes, encoding="utf-8") as f, open(path, "w", encoding="utf-8") as o:
        for line in f:
            p = line.rstrip("\t|\n").split("\t|\t")
            o.write(f"{p[0]}\t{p[1]}\t{p[2]}\t{clean(names.get(p[0], ''))}\n")
    c = pymysql.connect(**blast_db.cfg(), autocommit=True, local_infile=True).cursor()
    c.execute("DROP TABLE IF EXISTS blast_taxon_new")
    c.execute("CREATE TABLE blast_taxon_new LIKE blast_taxon")
    c.execute(f"LOAD DATA LOCAL INFILE '{path}' INTO TABLE blast_taxon_new CHARACTER SET utf8mb4 "
              "FIELDS TERMINATED BY '\\t' ESCAPED BY '' LINES TERMINATED BY '\\n' (taxid, parent, `rank`, name)")
    c.execute("SELECT COUNT(*) FROM blast_taxon"); old = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM blast_taxon_new"); new = c.fetchone()[0]
    if new < old * 0.95:
        raise SystemExit(f"new taxonomy has {new} taxa, old {old}: suspicious, not swapped")
    c.execute("RENAME TABLE blast_taxon TO blast_taxon_old, blast_taxon_new TO blast_taxon")
    c.execute("DROP TABLE blast_taxon_old")
    os.remove(path)
    open(MARKER, "w").close()
    log(f"blast_taxon reloaded: {old} -> {new} taxa (+{new - old}) in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
