#!/usr/bin/env python3
"""Build one partial DIAMOND database from nr_full.dmnd (concept: doc/multidb_daily.md).

  python3 nr_part_build.py --part p1_mammalia --taxon 40674
  python3 nr_part_build.py --part p2_metazoa --taxon 33208 --exclude 40674
  python3 nr_part_build.py --part p4_gamma --scan /mnt/archive/nr_split --groups gammaproteobacteria

--taxon selects in MariaDB by taxid (idx_taxid: one lookup per row, ~4 000 rows/s, fine for small parts).
--scan/--groups takes OIDs and accessions from the output of nr_acc_scan.py (one pass over blast_acc in
OID order); a part is a list of its groups.

A sequence (OID) belongs to the part when one of its accessions maps to a taxon below --taxon (and not
below --exclude). Its records are read from nr_full exactly as stored there (header ">acc1 >acc2 ...")
and piped into `diamond makedb` -- no FASTA on disk. The part carries its own taxonomy: a taxonmap of the
part's accessions from blast_acc plus nodes/names, so --taxonlist works inside the part.

Output in PART_DIR (default /home/gh/diamond/parts):
  <part>.dmnd        the database
  <part>.oids.npy    sorted uint64 OIDs; record number in the part = rank of the OID
  <part>.json        counts, hashes, criteria, timings
and a row in wagodb.blast_part. Holds the shared nr lock (like searches), so a daily append waits.
"""
import os, sys, json, glob, mmap, time, fcntl, argparse, subprocess
import numpy as np
import pymysql

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blast_db
from dmnd_getseq import Dmnd, UEBER as DECODE, luecken as gaps, satznummer as record_no

E = os.environ.get
DMND = E("DMND", "/home/gh/diamond/nr_full.dmnd")
PART_DIR = E("PART_DIR", "/home/gh/diamond/parts")
TAXDIR = E("TAXDIR", "/home/gh/diamond/taxdump")
LOCK = E("NR_LOCK", "/home/gh/.nr_dmnd.lock")
DIAMOND = E("DIAMOND_BIN", os.path.expanduser("~/build/diamond-master-61bbbe491bed/build/diamond"))  # needs --no-mask
VERIFY_N = 5000

DDL = """CREATE TABLE IF NOT EXISTS blast_part (
  part VARCHAR(32) NOT NULL PRIMARY KEY,
  dmnd VARCHAR(255) NOT NULL,
  criteria VARCHAR(255) NOT NULL,
  n_seq BIGINT UNSIGNED NOT NULL,
  n_acc BIGINT UNSIGNED NOT NULL,
  letters BIGINT UNSIGNED NOT NULL,
  dmnd_hash CHAR(32) NOT NULL,
  source_dmnd_hash CHAR(32) NOT NULL,
  seconds_select DOUBLE, seconds_build DOUBLE,
  built DATETIME NOT NULL
) DEFAULT CHARSET=ascii"""


def log(msg):
    print(time.strftime("[%F %T] ") + msg, flush=True)


def diamond_key(acc):
    """The accession key DIAMOND 2.2.8 joins on (util/sequence/sequence.cpp get_accession + :PDB= strip).
    'prf||2114404C' becomes '' there; several such entries make makedb fail with 'Duplicate keys: '."""
    i = acc.find(":PDB=")
    if i >= 0:
        acc = acc[:i]
    if acc.startswith("UniRef"):
        acc = acc[acc.find("_") + 1:]
    elif "|" in acc:
        i = acc.find("|")
        if acc.startswith("gi|"):
            acc = acc[acc.find("|", i + 1) + 1:]
            i = acc.find("|")
        acc = acc[i + 1:]
        j = acc.find("|")
        if j >= 0:
            acc = acc[:j]
    i = acc.rfind(".")
    if i >= 0:
        acc = acc[:i]
    return acc


def taxa_table(c, name, roots):
    c.execute(f"CREATE TEMPORARY TABLE {name} (taxid INT UNSIGNED PRIMARY KEY) ENGINE=MEMORY")
    if roots:
        ph = ",".join(["%s"] * len(roots))
        c.execute(f"INSERT IGNORE INTO {name} WITH RECURSIVE t AS (SELECT taxid FROM blast_taxon WHERE taxid IN ({ph}) "
                  "UNION SELECT k.taxid FROM blast_taxon k JOIN t ON k.parent = t.taxid AND k.taxid <> k.parent) "
                  "SELECT taxid FROM t", roots)
    c.execute(f"SELECT COUNT(*) FROM {name}")
    return c.fetchone()[0]


def scan_select(scan, groups, mp):
    """OIDs and taxonmap of the groups from nr_acc_scan output. Duplicate DIAMOND keys are found by a 64-bit
    hash in two passes (a set of strings would not fit in RAM for the large parts); the first accession in
    OID order keeps its key. -> (oids, n_acc, skipped)"""
    names = json.load(open(os.path.join(scan, "groups.json")))["names"]
    gids = [names.index(g) for g in groups]
    oids = []
    for z in sorted(glob.glob(os.path.join(scan, "chunks", "c*.npz"))):
        z = np.load(z)
        oids.append(z["oid"][np.isin(z["gid"], gids)])
    oids = np.unique(np.concatenate(oids)).astype(np.uint64)
    shards = sorted(f for g in gids for f in glob.glob(os.path.join(scan, "chunks", f"c*_g{g:02d}.tsv")))
    shards.sort(key=lambda f: os.path.basename(f))            # chunk order = OID order, groups within a chunk
    from array import array
    h = array("q")
    for f in shards:
        for line in open(f):
            h.append(hash(diamond_key(line[:line.index("\t")])))
    h = np.frombuffer(h, dtype=np.int64)
    n_acc = len(h)
    order = np.argsort(h, kind="stable")
    hs = h[order]
    dup = np.zeros(n_acc, dtype=bool)
    dup[order[1:][hs[1:] == hs[:-1]]] = True                 # all but the first occurrence of a key
    dup[h == hash("")] = True                                # empty key (e.g. 'prf||2114404C')
    del h, order, hs
    i = 0
    with open(mp, "w") as out:
        out.write("accession.version\ttaxid\n")
        for f in shards:
            for line in open(f):
                if not dup[i]:
                    out.write(line)
                i += 1
    return oids, n_acc, int(dup.sum())


def feed(dmnd, oids, pipe, log_every=20_000_000, t1=None):
    """Write the records of the sorted OIDs as FASTA into pipe, through an mmap of nr_full and its offset
    table (record = OID, blast_dmnd_luecke is empty). -> letters"""
    d = Dmnd(dmnd)
    tab = np.memmap(dmnd, dtype=np.dtype([("pos", "<u8"), ("len", "<u4"), ("pad", "<u4")]), mode="r",
                    offset=d.tabelle, shape=(d.n_seq + 1,))
    f = open(dmnd, "rb")
    mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    mm.madvise(mmap.MADV_SEQUENTIAL)
    letters = 0
    step = 200_000
    for b in range(0, len(oids), step):
        o = oids[b:b + step].astype(np.int64)
        pos = tab["pos"][o].tolist()
        end = tab["pos"][o + 1].tolist()
        ln = tab["len"][o].tolist()
        buf = []
        for p, e, n in zip(pos, end, ln):
            raw = mm[p:e]
            buf.append(b">" + raw[n + 2:raw.index(b"\0", n + 2)] + b"\n" + raw[1:n + 1].translate(DECODE) + b"\n")
            letters += n
        pipe.write(b"".join(buf))
        if t1 and (b + step) % log_every == 0:
            log(f"  {b + step} records, {time.time() - t1:.0f} s")
    mm.close()
    return letters


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", required=True)
    ap.add_argument("--taxon", default="", help="comma-separated root taxids (selection in MariaDB)")
    ap.add_argument("--scan", default="", help="output directory of nr_acc_scan.py")
    ap.add_argument("--groups", default="", help="comma-separated scan groups (with --scan)")
    ap.add_argument("--exclude", default="", help="comma-separated taxids whose sub-trees are left out")
    ap.add_argument("--threads", default="20")
    a = ap.parse_args()
    if bool(a.taxon) == bool(a.scan and a.groups):
        ap.error("either --taxon or --scan with --groups")
    inc = [int(x) for x in a.taxon.split(",") if x]
    groups = [g for g in a.groups.split(",") if g]
    exc = [int(x) for x in a.exclude.split(",") if x]
    os.makedirs(PART_DIR, exist_ok=True)
    out = os.path.join(PART_DIR, a.part)
    tmpdir = os.path.join(PART_DIR, "tmp_" + a.part)
    os.makedirs(tmpdir, exist_ok=True)

    lock = open(LOCK, "w")
    fcntl.flock(lock, fcntl.LOCK_SH)            # an append to nr_full (exclusive) waits until we are done
    con = pymysql.connect(**blast_db.cfg(), autocommit=True, read_timeout=7200)
    c = con.cursor()
    c.execute(DDL)
    c.execute("SELECT dmnd_hash FROM blast_release WHERE quelle='nr' AND aktiv=1 ORDER BY nr_datum DESC LIMIT 1")
    src_hash = c.fetchone()[0]
    t0 = time.time()
    mp = os.path.join(tmpdir, "taxonmap.tsv")
    if groups:
        log(f"{a.part}: groups {groups} from {a.scan}")
        oids, n_acc, skipped = scan_select(a.scan, groups, mp)
    else:
        oids, n_acc, skipped = select_mariadb(con, c, a.part, inc, exc, mp)
    log(f"taxonmap: {n_acc - skipped} accessions, {skipped} left out (empty or duplicate DIAMOND key)")
    t1 = time.time()
    log(f"{len(oids)} sequences, {n_acc} accessions selected in {t1 - t0:.0f} s")
    build(a, c, out, tmpdir, mp, oids, n_acc, inc, exc, groups, src_hash, t0, t1)


def select_mariadb(con, c, part, inc, exc, mp):
    log(f"{part}: taxa {inc} without {exc}: {taxa_table(c, 'pb_inc', inc)} taxa, {taxa_table(c, 'pb_exc', exc)} excluded")
    if exc:
        c.execute("DELETE i FROM pb_inc i JOIN pb_exc x ON x.taxid = i.taxid")

    # accessions of the part (in-part taxa only) -> taxonmap; the OIDs come from the same pass
    oids, n_acc, seen, skipped = [], 0, set(), 0
    cs = con.cursor(pymysql.cursors.SSCursor)
    cs.execute("SELECT a.oid, a.acc, a.taxid FROM pb_inc t JOIN blast_acc a ON a.taxid = t.taxid")
    with open(mp, "w") as f:
        f.write("accession.version\ttaxid\n")
        for oid, acc, taxid in cs:
            oids.append(oid)
            n_acc += 1
            k = diamond_key(acc)
            if not k or k in seen:          # DIAMOND cannot map these keys (empty or ambiguous)
                skipped += 1
                continue
            seen.add(k)
            f.write(f"{acc}\t{taxid}\n")
    cs.close()
    del seen
    return np.unique(np.array(oids, dtype=np.uint64)), n_acc, skipped


def verify(part, oids, letters, n=VERIFY_N):
    """The part must hold the records of nr_full unchanged and in OID order (record i = oids[i]): counts, letters,
    the first and last record and n random ones are compared byte for byte."""
    import random
    p, f = Dmnd(part), Dmnd(DMND)
    if p.n_seq != len(oids) or p.letters != letters:
        raise SystemExit(f"verify: part has {p.n_seq} sequences / {p.letters} letters, expected {len(oids)} / {letters}")
    idx = [0, len(oids) - 1] + random.sample(range(len(oids)), min(n, len(oids)))
    bad = [i for i in idx if p.satz(i) != f.satz(int(oids[i]))]
    if bad:
        raise SystemExit(f"verify: {len(bad)} of {len(idx)} records differ from nr_full, e.g. record {bad[0]}")
    log(f"verify: {len(idx)} records identical to nr_full")


def build(a, c, out, tmpdir, mp, oids, n_acc, inc, exc, groups, src_hash, t0, t1):
    d = Dmnd(DMND)
    if gaps(c):
        raise SystemExit("blast_dmnd_luecke not empty: record number != OID")
    # --no-mask (local DIAMOND patch): the records of nr_full are SEG-masked already; masking them again would
    # turn further residues into X that blast_mask cannot restore, and searches would differ from nr_full
    cmd = [DIAMOND, "makedb", "--no-mask", "--db", out + ".tmp", "--taxonmap", mp, "--taxonnodes", f"{TAXDIR}/nodes.dmp",
           "--taxonnames", f"{TAXDIR}/names.dmp", "--tmpdir", tmpdir, "--threads", a.threads]
    log("makedb: " + " ".join(cmd))
    mk = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=open(out + ".makedb.log", "w"),
                          stderr=subprocess.STDOUT, bufsize=16 << 20)
    letters = feed(DMND, oids, mk.stdin, t1=t1)
    mk.stdin.close()
    rc = mk.wait()
    t2 = time.time()
    mlog = open(out + ".makedb.log").read()
    if rc != 0:
        raise RuntimeError(f"makedb exit {rc}: {mlog[-1000:]}")
    dhash = [l for l in mlog.splitlines() if "Database hash" in l][0].split()[-1]
    verify(out + ".tmp.dmnd", oids, letters)
    os.replace(out + ".tmp.dmnd", out + ".dmnd")
    np.save(out + ".oids.npy.part.npy", oids)
    os.replace(out + ".oids.npy.part.npy", out + ".oids.npy")
    meta = {"part": a.part, "dmnd": out + ".dmnd", "taxon": inc, "exclude": exc, "groups": groups,
            "n_seq": int(len(oids)),
            "n_acc": n_acc, "letters": letters, "dmnd_hash": dhash, "source_dmnd_hash": src_hash,
            "source_letters": d.letters, "seconds_select": round(t1 - t0, 1), "seconds_build": round(t2 - t1, 1),
            "size_bytes": os.path.getsize(out + ".dmnd")}
    json.dump(meta, open(out + ".json", "w"), indent=1)
    c.execute("REPLACE INTO blast_part (part, dmnd, criteria, n_seq, n_acc, letters, dmnd_hash, source_dmnd_hash, "
              "seconds_select, seconds_build, built) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())",
              (a.part, out + ".dmnd", json.dumps({"taxon": inc, "exclude": exc, "groups": groups}), len(oids), n_acc, letters,
               dhash, src_hash, t1 - t0, t2 - t1))
    os.remove(mp)
    os.rmdir(tmpdir)
    log(f"done: {len(oids)} sequences, {letters} letters, {meta['size_bytes'] / 1e9:.1f} GB, "
        f"select {t1 - t0:.0f} s, build {t2 - t1:.0f} s, hash {dhash}")


if __name__ == "__main__":
    main()
