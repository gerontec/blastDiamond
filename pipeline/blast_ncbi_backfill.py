#!/usr/bin/env python3
"""Backfill the NCBI date for sequences of the nr release that appeared shortly before the snapshot.

blast_seq_ncbi only has a date for sequences appended by the daily delta (from 2026-09-16). Whatever GenBank
published between --from and the snapshot sits in the nr release without a date, so the new_days filter
(dmnd_preselect.py) cannot see it. This script reads the GenBank daily files (daily-nc, kept ~180 days) in
that period, looks the accessions up in blast_acc and adds a row (oid, LOCUS date) for every OID of the nr
release it finds. The creation date (createdate) is then fetched by blast_createdate_fill.py --von-vorn
--tage N as usual -- over ALL accessions of the OID, so that an old sequence with just one new accession
is not counted as new.

  blast_ncbi_backfill.py --from 2026-07-02 --to 2026-09-15            download files and insert rows
  blast_ncbi_backfill.py ... --count-only                             write nothing, only report the volume

RefSeq daily files only go back ~4 weeks and are not covered here.
Log: ~/python/blast_ncbi_backfill.log; files in WORK, finished ones get a .done marker.
"""
import argparse, gzip, os, re, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blast_db, pymysql

BASE = "https://ftp.ncbi.nlm.nih.gov/genbank/daily-nc/"
WORK = os.environ.get("BACKFILL_WORK", os.path.expanduser("~/nr_update/backfill"))
LOG = os.path.expanduser("~/python/blast_ncbi_backfill.log")
MON = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}


def log(m):
    line = time.strftime("[%F %T] ") + m
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def daily_files(date_from, date_to):
    html = urllib.request.urlopen(BASE, timeout=60).read().decode()
    r = re.findall(r'href="(nc\d{4}\.gnp\.gz)">[^<]*</a>\s+(\d{4}-\d{2}-\d{2})', html)
    return sorted({(d, n) for n, d in r if date_from <= d <= date_to})


def read_entries(path):
    """-> [(acc.version, 'YYYY-MM-DD')] from LOCUS/VERSION lines; sequences are skipped."""
    out, date = [], None
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("LOCUS"):
                d, m, y = line.split()[-1].split("-")
                date = f"{y}-{MON[m]:02d}-{int(d):02d}"
            elif line.startswith("VERSION"):
                out.append((line.split()[1], date))
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="date_from", required=True)
    p.add_argument("--to", dest="date_to", required=True)
    p.add_argument("--count-only", action="store_true")
    a = p.parse_args()
    os.makedirs(WORK, exist_ok=True)
    c = pymysql.connect(**blast_db.cfg(), autocommit=True).cursor()
    c.execute("SELECT COALESCE(MIN(first_oid), 0) FROM blast_import i JOIN blast_release r ON r.release_id = i.release_id "
              "WHERE r.quelle <> 'nr'")
    first_delta = c.fetchone()[0]                      # OIDs below = nr release
    files = daily_files(a.date_from, a.date_to)
    log(f"{len(files)} daily files {a.date_from}..{a.date_to}, nr release up to OID {first_delta - 1}"
        + (" (count only)" if a.count_only else ""))
    total = {"acc": 0, "found": 0, "oids": 0, "inserted": 0, "acc_of_oids": 0}
    t0 = time.time()
    for date, name in files:
        path = os.path.join(WORK, name)
        if os.path.exists(path + ".done") and not a.count_only:
            continue
        if not os.path.exists(path):
            urllib.request.urlretrieve(BASE + name, path + ".part")
            os.replace(path + ".part", path)
        entries = read_entries(path)
        latest = {}                                    # oid -> latest LOCUS date
        found = 0
        for i in range(0, len(entries), 1000):
            part = dict(entries[i:i + 1000])
            c.execute("SELECT acc, oid FROM blast_acc WHERE acc IN (" + ",".join(["%s"] * len(part)) + ")",
                      list(part))
            for acc, oid in c.fetchall():
                found += 1
                if oid < first_delta and part[acc] > latest.get(oid, ""):
                    latest[oid] = part[acc]
        inserted = 0
        if latest:
            oids = sorted(latest)
            for i in range(0, len(oids), 5000):
                t = oids[i:i + 5000]
                c.execute("SELECT SUM(n_acc) FROM blast_seq WHERE oid IN (" + ",".join(["%s"] * len(t)) + ")", t)
                total["acc_of_oids"] += int(c.fetchone()[0] or 0)
                if not a.count_only:
                    inserted += c.executemany("INSERT IGNORE INTO blast_seq_ncbi (oid, ncbi_datum) VALUES (%s, %s)",
                                              [(o, latest[o]) for o in t])
        total["acc"] += len(entries)
        total["found"] += found
        total["oids"] += len(latest)
        total["inserted"] += inserted
        if not a.count_only:
            open(path + ".done", "w").close()
            os.remove(path)
        log(f"{name} ({date}): {len(entries)} accessions, {found} in blast_acc, {len(latest)} OIDs of the nr release, "
            f"{inserted} inserted")
    log(f"total after {time.time() - t0:.0f} s: {total}")


if __name__ == "__main__":
    main()
