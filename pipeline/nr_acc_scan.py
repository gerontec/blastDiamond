#!/usr/bin/env python3
"""One pass over wagodb.blast_acc in primary-key (OID) order: every accession is assigned to a taxon group
and written to that group's taxonmap shard; the (OID, group) pairs are kept. From the output every cut of nr
into partial databases (nr_part_build) can be evaluated and assembled without querying MariaDB again.

Reading by taxid (idx_taxid) needs one clustered-index lookup per row in the compressed table, ~4 000 rows/s;
reading in OID order is sequential and runs in parallel over OID ranges.

  python3 nr_acc_scan.py --out /mnt/archive/nr_split                  whole nr
  python3 nr_acc_scan.py --out /mnt/archive/nr_split_test --oid-to 2000000    test on a range

Output in --out:
  groups.json                     group id -> name, root taxid
  chunks/c<k>_g<gid>.tsv          'accession<TAB>taxid' of group gid in OID chunk k (OID order inside)
  chunks/c<k>.npz                 unique (oid uint32, gid uint8) pairs of chunk k
  summary.json                    per group: sequences, accessions, letters (from the nr_full offset table)
Holds the shared nr lock (like searches), so a daily append waits.
"""
import os, sys, json, time, fcntl, argparse
from multiprocessing import Pool
import numpy as np
import pymysql

sys.path.insert(0, os.path.expanduser("~/python"))
import blast_db
from dmnd_getseq import Dmnd, SATZ as REC

E = os.environ.get
DMND = E("DMND", "/home/gh/diamond/nr_full.dmnd")
LOCK = E("NR_LOCK", "/home/gh/.nr_dmnd.lock")

# group roots; the nearest root on the lineage wins (human -> mammalia, not metazoa)
ROOTS = [
    ("mammalia", 40674), ("metazoa", 33208), ("fungi", 4751), ("plants", 33090), ("eukaryota", 2759),
    ("viruses", 10239), ("archaea", 2157),
    ("gammaproteobacteria", 1236), ("alphaproteobacteria", 28211), ("betaproteobacteria", 28216),
    ("pseudomonadota", 1224), ("bacillota", 1239), ("actinomycetota", 201174), ("bacteroidota", 976),
    ("cyanobacteriota", 1117), ("campylobacterota", 29547), ("chloroflexota", 200795),
    ("spirochaetota", 203691), ("planctomycetota", 203682), ("verrucomicrobiota", 74201),
    ("mycoplasmatota", 544448), ("bacteria", 2),
]
OTHER, NONE = len(ROOTS), len(ROOTS) + 1        # in the taxonomy but under no root / taxid unknown or 0
NAMES = [r[0] for r in ROOTS] + ["other", "none"]


def log(msg):
    print(time.strftime("[%F %T] ") + msg, flush=True)


def group_table(c):
    """gid per taxid as a list indexed by taxid (NONE where unknown)."""
    c.execute("SELECT taxid, parent FROM blast_taxon")
    return group_table_from_parent(dict(c.fetchall()))


def nodes_parent(nodes_dmp):
    """taxid -> parent from an NCBI nodes.dmp."""
    parent = {}
    for line in open(nodes_dmp):
        f = line.split("\t|\t", 2)
        parent[int(f[0])] = int(f[1])
    return parent


def group_table_from_parent(parent):
    root_gid = {t: g for g, (_, t) in enumerate(ROOTS)}
    gid = {}
    for t in parent:
        path, x = [], t
        while x not in gid:
            if x in root_gid:
                gid[x] = root_gid[x]
                break
            p = parent.get(x)
            if p is None or p == x:              # reached the root of the tree (1) without a group
                gid[x] = OTHER
                break
            path.append(x)
            x = p
        for y in path:
            gid[y] = gid[x]
    table = [NONE] * (max(parent) + 1)
    for t, g in gid.items():
        table[t] = g
    return table


G = None          # worker globals
CON = None


def init(table):
    global G, CON
    G = table
    CON = pymysql.connect(**blast_db.cfg(), read_timeout=7200)


def scan_chunk(args):
    k, lo, hi, out = args
    cs = CON.cursor(pymysql.cursors.SSCursor)
    cs.execute("SELECT oid, taxid, acc FROM blast_acc WHERE oid >= %s AND oid < %s", (lo, hi))
    files, oids, gids, n = {}, [], [], 0
    n_tab = len(G)
    for oid, taxid, acc in cs:
        g = G[taxid] if 0 < taxid < n_tab else NONE
        f = files.get(g)
        if f is None:
            f = files[g] = open(os.path.join(out, "chunks", f"c{k:04d}_g{g:02d}.tsv"), "w", buffering=1 << 20)
        f.write(f"{acc}\t{taxid}\n")
        oids.append(oid)
        gids.append(g)
        n += 1
    cs.close()
    for f in files.values():
        f.close()
    key = np.unique(np.array(oids, dtype=np.uint64) << np.uint64(8) | np.array(gids, dtype=np.uint64))
    np.savez(os.path.join(out, "chunks", f"c{k:04d}.npz.part.npz"),
             oid=(key >> np.uint64(8)).astype(np.uint32), gid=(key & np.uint64(255)).astype(np.uint8))
    os.replace(os.path.join(out, "chunks", f"c{k:04d}.npz.part.npz"), os.path.join(out, "chunks", f"c{k:04d}.npz"))
    acc_per_g = np.bincount(np.array(gids, dtype=np.int64), minlength=len(NAMES)) if gids else np.zeros(len(NAMES), int)
    return k, n, acc_per_g.tolist()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--chunk", type=int, default=10_000_000, help="OIDs per query")
    ap.add_argument("--oid-to", type=int, default=0, help="only OIDs below this (test)")
    a = ap.parse_args()
    os.makedirs(os.path.join(a.out, "chunks"), exist_ok=True)

    lock = open(LOCK, "w")
    fcntl.flock(lock, fcntl.LOCK_SH)
    con = pymysql.connect(**blast_db.cfg())
    c = con.cursor()
    t0 = time.time()
    table = group_table(c)
    json.dump({"names": NAMES, "roots": dict(ROOTS)}, open(os.path.join(a.out, "groups.json"), "w"), indent=1)
    d = Dmnd(DMND)
    end = a.oid_to or d.n_seq
    c.execute("SELECT COUNT(*) FROM blast_dmnd_luecke")
    if c.fetchone()[0]:
        raise SystemExit("blast_dmnd_luecke not empty: record number != OID, letters per OID would be wrong")
    log(f"taxonomy: {len(table)} taxid slots in {time.time() - t0:.0f} s; scanning OIDs 0..{end - 1}, "
        f"{a.workers} workers, chunks of {a.chunk}")

    tasks = [(k, lo, min(lo + a.chunk, end), a.out) for k, lo in enumerate(range(0, end, a.chunk))]
    acc = np.zeros(len(NAMES), dtype=np.int64)
    rows = 0
    t1 = time.time()
    with Pool(a.workers, initializer=init, initargs=(table,)) as pool:
        for i, (k, n, per_g) in enumerate(pool.imap_unordered(scan_chunk, tasks), 1):
            rows += n
            acc += per_g
            el = time.time() - t1
            log(f"chunk {k} done ({i}/{len(tasks)}): {rows} rows, {rows / el:.0f} rows/s, "
                f"eta {el / i * (len(tasks) - i) / 60:.0f} min")

    # sequences and letters per group from the pairs and the nr_full offset table (16 bytes per record)
    lens = np.memmap(DMND, dtype=np.dtype([("pos", "<u8"), ("len", "<u4"), ("pad", "<u4")]), mode="r",
                     offset=d.tabelle, shape=(d.n_seq + 1,))["len"]
    n_seq = np.zeros(len(NAMES), dtype=np.int64)
    letters = np.zeros(len(NAMES), dtype=np.int64)
    multi = 0
    for k, _, _, _ in tasks:
        z = np.load(os.path.join(a.out, "chunks", f"c{k:04d}.npz"))
        oid, gid = z["oid"], z["gid"]
        n_seq += np.bincount(gid, minlength=len(NAMES))
        letters += np.bincount(gid, weights=lens[oid].astype(np.float64), minlength=len(NAMES)).astype(np.int64)
        multi += len(oid) - len(np.unique(oid))
    summary = {"oid_end": end, "rows": rows, "seconds": round(time.time() - t0), "pairs_extra_multi_group": int(multi),
               "groups": {NAMES[g]: {"n_seq": int(n_seq[g]), "n_acc": int(acc[g]), "letters": int(letters[g]),
                                     "est_gb": round(float(letters[g]) * d.f.seek(0, 2) / d.letters / 1e9, 1) if d.letters else None}
                          for g in range(len(NAMES))}}
    json.dump(summary, open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    for nm, v in sorted(summary["groups"].items(), key=lambda x: -x[1]["letters"]):
        log(f"  {nm:20s} {v['n_seq']:>12} seq {v['n_acc']:>12} acc {v['letters'] / 1e9:8.2f} G letters ~{v['est_gb']} GB")
    log(f"done: {rows} rows, {multi} extra (OID, group) pairs from multi-group sequences, {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
