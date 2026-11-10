#!/usr/bin/env python3
"""Write statistics.md: how many sequences each nr part holds, its taxon groups and the whole nr.

  python3 nr_parts_stats.py [--out statistics.md] [--scan /mnt/archive/nr_split]

Sources: blast_part (built parts), blast_part_group (group -> part), the newest blast_release row with
dmnd_letters (whole nr) and, for the per-group counts and for parts not built yet, summary.json of
nr_acc_scan.py (the one-time scan of 2026-11-10; groups do not change with daily appends there).
"""
import os, sys, json, time, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blast_db, pymysql


def n(x):
    return f"{x:,}".replace(",", " ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="statistics.md")
    ap.add_argument("--scan", default="/mnt/archive/nr_split")
    a = ap.parse_args()
    c = pymysql.connect(**blast_db.cfg()).cursor()
    c.execute("SELECT part, dmnd, n_seq, n_acc, letters, built FROM blast_part WHERE part REGEXP '^p[0-9]+_' ORDER BY part")
    built = {r[0]: r[1:] for r in c.fetchall()}
    c.execute("SELECT grp, root_taxid, part FROM blast_part_group ORDER BY part, grp")
    groups = c.fetchall()
    c.execute("SELECT dmnd_sequenzen, dmnd_letters, dmnd_hash, nr_datum FROM blast_release "
              "WHERE dmnd_letters IS NOT NULL ORDER BY release_id DESC LIMIT 1")
    whole = c.fetchone()
    scan = json.load(open(os.path.join(a.scan, "summary.json")))
    sg = scan["groups"]

    parts = sorted({p for _, _, p in groups})
    L = ["# nr statistics", "",
         f"Generated {time.strftime('%Y-%m-%d %H:%M')} by `pipeline/nr_parts_stats.py`. The NCBI nr is held in "
         f"{len(parts)} partial DIAMOND databases (see `doc/multidb_daily.md`).", ""]
    if whole:
        L += [f"**Whole nr:** {n(int(whole[0]))} sequences, {n(int(whole[1]))} letters "
              f"(state {whole[2]}, {whole[3]:%Y-%m-%d}).", ""]
    else:
        L += [f"**Whole nr:** {n(scan['oid_end'])} sequences (scan; the size in blast_release is registered "
              "when all parts are built).", ""]

    L += ["## Parts", "",
          "| part | sequences | accessions | letters | size | status |",
          "|---|---:|---:|---:|---:|---|"]
    tot_seq = tot_acc = tot_let = tot_size = 0
    for p in parts:
        gs = [g for g, _, pp in groups if pp == p]
        if p in built and os.path.exists(built[p][0]):
            d, ns, na, nl, when = built[p]
            size = os.path.getsize(d)
            status = f"built {when:%Y-%m-%d %H:%M}"
            ns, na, nl = int(ns), int(na), int(nl)
        else:      # not built yet: sum over the groups (sequences in two of its groups count twice)
            ns = sum(sg[g]["n_seq"] for g in gs)
            na = sum(sg[g]["n_acc"] for g in gs)
            nl = sum(sg[g]["letters"] for g in gs)
            size = None
            status = "building (counts from the scan)"
        tot_seq += ns
        tot_acc += na
        tot_let += nl
        tot_size += size or 0
        L.append(f"| `{p}` | {n(ns)} | {n(na)} | {n(nl)} | "
                 f"{f'{size / 1e9:.1f} GB' if size else '–'} | {status} |")
    L.append(f"| **sum** | **{n(tot_seq)}** | **{n(tot_acc)}** | **{n(tot_let)}** | "
             f"**{tot_size / 1e9:.1f} GB** | |")
    ref = int(whole[0]) if whole else scan["oid_end"]
    L += ["", f"The sum is larger than the whole nr by {n(tot_seq - ref)} sequences: a sequence whose accessions "
          "belong to groups of different parts is stored in each of them "
          f"({n(scan['pairs_extra_multi_group'])} extra (sequence, group) pairs in the scan). "
          "Accessions: a part maps only the accessions of its own groups.", ""]

    L += ["## Taxon groups", "",
          "A sequence goes to the group of the nearest group root on its lineage (human -> mammalia, not "
          "metazoa). Counts from the scan of " + time.strftime("%Y-%m-%d", time.localtime(
              os.path.getmtime(os.path.join(a.scan, "summary.json")))) + ".", "",
          "| group | root taxid | part | sequences | accessions | letters |",
          "|---|---:|---|---:|---:|---:|"]
    for g, root, p in sorted(groups, key=lambda x: (x[2], -sg[x[0]]["n_seq"])):
        v = sg[g]
        L.append(f"| {g} | {root or '–'} | `{p}` | {n(v['n_seq'])} | {n(v['n_acc'])} | {n(v['letters'])} |")
    L += ["", "`other`: in the taxonomy but under no group root; `none`: taxid unknown or 0 (e.g. PDB chains "
          "without organism).", ""]
    open(a.out, "w").write("\n".join(L))
    print(f"{a.out}: {len(parts)} parts, {len(groups)} groups")


if __name__ == "__main__":
    main()
