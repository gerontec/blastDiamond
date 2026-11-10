#!/usr/bin/env python3
"""Runtime of a single-sequence DIAMOND search on the largest nr part, as the API worker runs it.

  python3 nr_part_bench.py [--part p4_pseudomonadota] [--sens fast] [--n 12] [--out benchmark.md]

Queries: one sequence per diamond run, lengths spread from ~50 to ~3000 aa, taken from different parts (so some
have close hits in the benchmarked part and some do not). Settings as the worker uses them for a form search:
blastp, e-value 0.001, 25 targets per query, 20 threads, --block-size 2, --dbsize of the whole nr. Peak memory via
/usr/bin/time. Writes a markdown report with every run, average and maximum, and the load of the machine.
"""
import os, sys, time, json, argparse, subprocess, tempfile
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blast_db, pymysql
from nr_parts import NrParts

DIAMOND = os.environ.get("DIAMOND_BIN", os.path.expanduser("~/.local/share/mamba/envs/diamond/bin/diamond"))
TMP = os.environ.get("JOB_TMP", "/mnt/archive/blast_api_tmp")
LENGTHS = [50, 100, 150, 250, 350, 500, 700, 900, 1200, 1600, 2200, 3200]
SOURCES = ["p1_mammalia", "p4_pseudomonadota", "p5_bacillati", "p3_viruses_archaea_other", "p2_eukaryota_other",
           "p6_bacteria_other"]


def pick_queries(nr, n, seed=7):
    """One sequence per target length, drawn from the parts in turn: random records until the length is within 15 %
    and the sequence has no masked X (a user would paste the real sequence)."""
    rng = np.random.default_rng(seed)
    out = []
    for k, want in enumerate(LENGTHS[:n]):
        part = SOURCES[k % len(SOURCES)]
        d, o = nr.dmnd(part), nr.oids(part)
        for _ in range(200000):
            i = int(rng.integers(0, d.n_seq))
            name, seq = d.satz(i)
            if abs(len(seq) - want) <= want * 0.15 and "X" not in seq:
                out.append({"source": part, "oid": int(o[i]), "acc": name.split()[0], "length": len(seq), "seq": seq})
                break
    return out


def load():
    with open("/proc/loadavg") as f:
        return f.read().split()[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="p4_pseudomonadota")
    ap.add_argument("--sens", default="fast")
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--out", default="benchmark.md")
    a = ap.parse_args()
    c = pymysql.connect(**blast_db.cfg()).cursor()
    nr = NrParts(c)
    dmnd, letters = next((d, l) for p, d, l, _ in nr.parts if p == a.part)
    qs = pick_queries(nr, a.n)
    print(f"{a.part}: {dmnd}, {letters} letters; {len(qs)} queries", flush=True)
    rows = []
    for q in qs:
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            qf, of, tf = (os.path.join(d, x) for x in ("q.fa", "out.tsv", "rss.txt"))
            open(qf, "w").write(f">{q['acc']}\n{q['seq']}\n")
            cmd = ["/usr/bin/time", "-f", "%M", "-o", tf, DIAMOND, "blastp", "--query", qf, "--db", dmnd, "--out", of,
                   "--evalue", "0.001", "--max-target-seqs", "25", f"--{a.sens}", "--threads", "20",
                   "--block-size", "2", "--dbsize", str(nr.letters), "--tmpdir", d,
                   "--outfmt", "6", "qseqid", "sseqid", "pident", "evalue", "bitscore"]
            l0 = load()
            t = time.time()
            r = subprocess.run(cmd, capture_output=True, text=True)
            secs = time.time() - t
            hits = sum(1 for _ in open(of)) if os.path.exists(of) else 0
            best = open(of).readline().split("\t")[2] if hits else ""
            rss = int(open(tf).read().split()[-1]) / 1048576 if os.path.exists(tf) else None
        row = {**{k: q[k] for k in ("source", "acc", "length")}, "seconds": round(secs, 1), "hits": hits,
               "best_pident": best, "ram_gb": round(rss, 2) if rss else None, "load_before": l0, "rc": r.returncode}
        rows.append(row)
        print(json.dumps(row), flush=True)
        if r.returncode:
            print(r.stderr[-500:], flush=True)

    ok = [r for r in rows if r["rc"] == 0]
    s = np.array([r["seconds"] for r in ok])
    L = ["# DIAMOND benchmark: single query on the largest nr part", "",
         f"Measured {time.strftime('%Y-%m-%d %H:%M')} by `build/nr_part_bench.py` on the database server "
         "(20 threads, 62 GB RAM, NVMe).", "",
         f"- database: `{a.part}` ({letters / 1e9:.1f} billion letters, {os.path.getsize(dmnd) / 1e9:.1f} GB), "
         "the largest of the six nr parts",
         f"- search: `diamond blastp --{a.sens}`, e-value 0.001, 25 targets per query, `--block-size 2`, "
         "`--dbsize` of the whole nr (E-values as in a search of all of nr)",
         f"- {len(ok)} single-sequence queries, one diamond run each, lengths {min(r['length'] for r in ok)}-"
         f"{max(r['length'] for r in ok)} aa from all six parts", "",
         "## Result", "",
         f"| | seconds |", "|---|---:|",
         f"| **average** | **{s.mean():.1f}** |", f"| median | {np.median(s):.1f} |",
         f"| **maximum** | **{s.max():.1f}** |", f"| minimum | {s.min():.1f} |", "",
         f"Peak memory {max(r['ram_gb'] or 0 for r in ok):.1f} GB.", "",
         "## Runs", "",
         "| query | from part | length (aa) | seconds | hits | best identity % | peak RAM (GB) | load before |",
         "|---|---|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        L.append(f"| {r['acc']} | `{r['source']}` | {r['length']} | {r['seconds']} | {r['hits']} | "
                 f"{r['best_pident'] or '–'} | {r['ram_gb']} | {r['load_before']} |")
    L += ["", "A search of the whole nr runs the six parts one after another; this part holds 26 % of the letters.", ""]
    open(a.out, "w").write("\n".join(L))
    print(f"average {s.mean():.1f} s, max {s.max():.1f} s -> {a.out}")


if __name__ == "__main__":
    main()
