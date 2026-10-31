#!/usr/bin/env python3
"""Report organisms that entered our nr (DIAMOND, daily delta) in the last N days and were never present before.

usage: rep_newOrgani.py [DAYS=15] [--tsv FILE]
Groups by domain, highlights viruses / eukaryotes / archaea, lists the biggest new bacteria,
prints the runtime of every step at the end.
"""
import argparse
import collections
import json
import sys
import time
import urllib.request

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import blast_db  # noqa: E402
import pymysql  # noqa: E402

TIMES = []


def step(name, t0):
    TIMES.append((name, time.time() - t0))
    return time.time()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("days", nargs="?", type=int, default=15)
    ap.add_argument("--tsv", help="also write every new organism with lineage as TSV")
    ap.add_argument("--offline", action="store_true", help="no NCBI lookup for taxa missing in the taxdump")
    a = ap.parse_args()
    start = t = time.time()
    cur = pymysql.connect(**blast_db.cfg()).cursor()

    # all imports of the window; everything before first_oid existed earlier
    cur.execute("SELECT MIN(first_oid), COUNT(*), SUM(n_seq) FROM blast_import "
                "WHERE geladen >= NOW() - INTERVAL %s DAY", (a.days,))
    first, n_imports, n_seq = cur.fetchone()
    if first is None:
        print(f"no imports in the last {a.days} days")
        return
    t = step("window", t)

    cur.execute("""SELECT a.taxid, COUNT(*) FROM blast_acc a
                   JOIN blast_import i ON a.oid BETWEEN i.first_oid AND i.last_oid
                   WHERE i.geladen >= NOW() - INTERVAL %s DAY GROUP BY a.taxid""", (a.days,))
    recent = dict(cur.fetchall())
    t = step("taxa in window", t)

    new = []
    for tx, n in recent.items():                       # uses idx_taxid, one probe per taxon
        cur.execute("SELECT 1 FROM blast_acc WHERE taxid=%s AND oid < %s LIMIT 1", (tx, first))
        if not cur.fetchone():
            new.append((tx, n))
    t = step("never seen before", t)

    cache = {}

    def node(tx):
        if tx not in cache:
            cur.execute("SELECT parent, `rank`, name FROM blast_taxon WHERE taxid=%s", (tx,))
            cache[tx] = cur.fetchone()
        return cache[tx]

    rows = []
    for tx, n in new:
        lin, x, hops = {}, tx, 0
        while x and hops < 60:
            r = node(x)
            if not r:
                break
            parent, rank, name = r
            lin.setdefault(rank, name)
            if parent == x:
                break
            x, hops = parent, hops + 1
        own = node(tx) or (None, "?", f"taxid {tx}")
        dom = (lin.get("superkingdom") or lin.get("domain") or lin.get("realm")
               or lin.get("acellular root") or "unknown")
        if dom in ("Duplodnaviria", "Riboviria", "Varidnaviria", "Monodnaviria", "Adnaviria", "Ribozyviria"):
            dom = "Viruses"
        rows.append(dict(domain=dom, phylum=lin.get("phylum", ""), cls=lin.get("class", ""),
                         order=lin.get("order", ""), rank=own[1], name=own[2], seqs=n, taxid=tx))
    t = step("lineage", t)

    # taxa missing in the local taxdump (NCBI lists many fresh strain taxids as deleted): ask NCBI directly
    unknown = [r for r in rows if r["domain"] == "unknown"]
    if unknown and not a.offline:
        info = {}
        ids = [str(r["taxid"]) for r in unknown]
        for i in range(0, len(ids), 200):
            url = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=taxonomy&retmode=json&id="
                   + ",".join(ids[i:i + 200]))
            try:
                res = json.load(urllib.request.urlopen(url, timeout=30))["result"]
                info.update({u: res[u] for u in res.get("uids", [])})
            except (OSError, ValueError, KeyError):
                break
            time.sleep(0.4)                            # NCBI: max 3 requests/s without API key
        division = {"bacteria": "Bacteria", "proteobacteria": "Bacteria", "firmicutes": "Bacteria",
                    "gram-positive": "Bacteria", "cyanobacteria": "Bacteria", "archaea": "Archaea",
                    "viruses": "Viruses", "phages": "Viruses", "plants": "Eukaryota", "fungi": "Eukaryota",
                    "insects": "Eukaryota", "fishes": "Eukaryota", "invertebrates": "Eukaryota"}
        for r in unknown:
            d = info.get(str(r["taxid"]))
            if not d or not d.get("scientificname"):
                continue
            r["name"], r["rank"] = d["scientificname"], d.get("rank") or r["rank"]
            div = (d.get("division") or "").lower()
            r["domain"] = next((v for k, v in division.items() if k in div), "unknown")
            r["cls"] = d.get("division", "")
            r["ncbi"] = True
        t = step("NCBI lookup", t)

    by_dom = collections.Counter(r["domain"] for r in rows)
    print(f"New organisms in our nr, last {a.days} days")
    print(f"  imports: {n_imports}, new sequences: {n_seq}, taxa with new sequences: {len(recent)}, "
          f"never seen before: {len(rows)}")
    print("  by domain: " + ", ".join(f"{d} {n}" for d, n in by_dom.most_common()))
    for dom in ("Viruses", "Eukaryota", "Archaea"):
        sel = sorted((r for r in rows if r["domain"] == dom), key=lambda r: -r["seqs"])
        if sel:
            print(f"\n{dom} ({len(sel)})")
            for r in sel:
                print(f"  {r['phylum'][:18]:<18} {r['cls'][:20]:<20} {r['name'][:52]:<52} {r['seqs']:>6} seq")
    bac = [r for r in rows if r["domain"] == "Bacteria"]
    if bac:
        print(f"\nBacteria ({len(bac)}), phyla: " + ", ".join(
            f"{p or '?'} {n}" for p, n in collections.Counter(r["phylum"] for r in bac).most_common(8)))
        for r in sorted(bac, key=lambda r: -r["seqs"])[:15]:
            print(f"  {r['phylum'][:18]:<18} {r['cls'][:20]:<20} {r['name'][:52]:<52} {r['seqs']:>6} seq")
    unk = [r for r in rows if r["domain"] == "unknown"]
    if unk:
        print(f"\nwithout lineage ({len(unk)}) - taxid deleted at NCBI (delnodes.dmp), sequences carry an obsolete taxid")
    via = sum(1 for r in rows if r.get("ncbi"))
    if via:
        print(f"({via} organisms named via NCBI lookup: missing in the local taxdump, class column = NCBI division)")
    if a.tsv:
        with open(a.tsv, "w", encoding="utf-8") as f:
            f.write("domain\tphylum\tclass\torder\trank\tname\tseqs\ttaxid\n")
            for r in sorted(rows, key=lambda r: (r["domain"], -r["seqs"])):
                f.write("\t".join(str(r[k]) for k in ("domain", "phylum", "cls", "order", "rank", "name",
                                                      "seqs", "taxid")) + "\n")
    t = step("report", t)
    print("\nruntime: " + ", ".join(f"{n} {s:.1f}s" for n, s in TIMES) + f" | total {time.time() - start:.1f}s")


if __name__ == "__main__":
    main()
