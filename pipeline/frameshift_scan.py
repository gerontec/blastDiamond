#!/usr/bin/env python3
"""What protein comes out when the ribosome slips: predicted frameshift products of a coding sequence, searched
against our protein databases.

Two mechanisms are described for humans, and both produce a protein that is normal at the front and foreign
behind the slip:

  +1 / -1 ribosomal frameshifting at a slippery site. N1-methylpseudouridine (the modified base of mRNA
  therapeutics) makes the ribosome slip at runs of the same base (Mulroney et al., Nature 625, 189-194, 2024).
  molecular misreading: a dinucleotide deletion in the mRNA at a GAGAG motif, which shifts the frame from there
  on (van Leeuwen et al., 1998; the product UBB+1 is ubiquitin plus a 20 aa extension).

This script does not guess where a ribosome slips. It computes **every** product a slip at a given kind of site
would produce, says where the site is, and then asks the only question we can answer with data: does that
product resemble a protein that is already known?

  frameshift_scan.py --in cds.fna --shift +1 --sites u-run        products of +1 slips at runs of >= 3 U
  frameshift_scan.py --in cds.fna --sites gagag --delete 2        molecular misreading: two bases deleted
  frameshift_scan.py --in cds.fna --sites all --shift -1          every codon boundary, -1
  ... --db vf        search the 17 831 curated reference proteins (seconds), flags from blast_flag_bit
  ... --db p1        search one nr part, e.g. p1_mammalia (minutes)
  ... --db nr        search all six nr parts, E-values as in a whole-nr search (long: ~15 min per query)

Output: <out>/products.faa (full products), <out>/novel.faa (only the part behind the slip), <out>/products.tsv
and the report on stdout. Nothing is written to the database - this is an analysis, not a pipeline.

Env: VF_DMND, DIAMOND_BIN, FS_OUT (default /mnt/archive/gh_tmp/frameshift).
"""
import argparse, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.expanduser("~/python"))

VF = os.environ.get("VF_DMND", "/home/gh/diamond/vf/vf.dmnd")
DIAMOND = os.environ.get("DIAMOND_BIN", os.path.expanduser("~/build/diamond-master-61bbbe491bed/build/diamond"))
OUT = os.environ.get("FS_OUT", "/mnt/archive/gh_tmp/frameshift")
MIN_NOVEL = 10          # a product whose foreign part is shorter than this says nothing
T0 = time.time()

# standard genetic code (NCBI table 1), built from the canonical ordering T,C,A,G so it can be checked by eye
_BASES = "TCAG"
_AAS = "FFLLSSSSYY**CC*WLLLLPPPPHHQQRRRRIIIMTTTTNNKKSSRRVVVVAAAADDEEGGGG"
CODE = {b1 + b2 + b3: _AAS[i]
        for i, (b1, b2, b3) in enumerate((x, y, z) for x in _BASES for y in _BASES for z in _BASES)}
assert len(CODE) == 64 and CODE["ATG"] == "M" and CODE["TGA"] == "*" and CODE["TGG"] == "W"


def log(msg):
    print(f"[{time.time() - T0:7.1f}s] {msg}", flush=True)


def translate(nt, stop_at_stop=True):
    aa = []
    for i in range(0, len(nt) - 2, 3):
        a = CODE.get(nt[i:i + 3].upper(), "X")
        if a == "*" and stop_at_stop:
            break
        aa.append(a)
    return "".join(aa)


def read_fasta(path):
    name, seq, out = None, [], []
    for line in open(path):
        line = line.strip()
        if line.startswith(">"):
            if name:
                out.append((name, "".join(seq)))
            name, seq = line[1:].split()[0], []
        elif line:
            seq.append(re.sub(r"[^ACGTUacgtu]", "", line).upper().replace("U", "T"))
    if name:
        out.append((name, "".join(seq)))
    return out


def sites(seq, kind, min_run=3, flank=0):
    """Where a slip or a deletion is considered, as (start, end, label). start..end is the whole motif or run:
    inside it every position is possible, and which one it is decides the residue at the junction - the deposited
    UBB+1 (PDB 3K9O) carries G there, the first position of the GAGAG motif would give W."""
    if kind == "all":
        return [(i, i + 1, "codon boundary") for i in range(3, len(seq) - 6, 3)]
    if kind == "gagag":
        # "in or adjacent to GAGAG motifs" (van Leeuwen): the deposited UBB+1 comes from a deletion three bases
        # BEHIND the motif, so the flank has to be searched as well, not only the motif itself
        return [(max(0, m.start() - flank), min(len(seq), m.end() + flank), f"GAGAG motif at {m.start() + 1}")
                for m in re.finditer(r"GAGAG", seq)]
    base = {"u-run": "T", "a-run": "A", "c-run": "C", "g-run": "G"}.get(kind)
    if not base:
        sys.exit(f"unknown --sites {kind}")
    return [(m.start(), m.end(), f"{len(m.group(0))} x {('U' if base == 'T' else base)} at {m.start() + 1}")
            for m in re.finditer(base + "{" + str(min_run) + ",}", seq)]


def products(name, seq, kind, shift, delete, min_novel, flank=0):
    """One product per site. The mRNA is changed and then read from the start, which is what actually happens:
    a deletion or a slip moves the frame for everything behind it. The product is therefore identical to the
    normal protein up to the site and foreign from there on, and the split is found by comparing the two."""
    normal = translate(seq)
    out = []
    spots = [(pos, label) for start, end, label in sites(seq, kind, flank=flank)
             for pos in range(start, max(start + 1, end - (delete - 1 if delete else 0)))]
    for pos, label in spots:
        if delete:                                # molecular misreading: the bases are gone from the mRNA
            mut = seq[:pos] + seq[pos + delete:]
            how = f"delta{delete}_nt{pos + 1}"
        else:                                     # ribosomal frameshifting at the end of the codon at the site
            cut = (pos // 3) * 3 + 3
            mut = seq[:cut] + seq[cut + 1:] if shift > 0 else seq[:cut] + seq[cut - 1:]
            how = f"{shift:+d}_after_codon{cut // 3}"
        prot = translate(mut)
        i = 0
        while i < min(len(prot), len(normal)) and prot[i] == normal[i]:
            i += 1
        novel = prot[i:]
        if len(novel) < min_novel:
            continue
        out.append({"name": f"{name}|{how}".replace(" ", "_"), "site": label, "how": how, "pos": pos,
                    "head_aa": i, "novel_aa": len(novel), "protein": prot, "novel": novel})
    # the same foreign tail can come from several sites; keep the first and count the rest
    seen, uniq = {}, []
    for p in out:
        k = p["novel"]
        if k in seen:
            seen[k]["same_tail"] += 1
            continue
        p["same_tail"] = 1
        seen[k] = p
        uniq.append(p)
    return normal, uniq


def write_fasta(path, items, key):
    with open(path, "w") as f:
        for p in items:
            f.write(f">{p['name']}\n{p[key]}\n")


def db_args(which):
    if which == "vf":
        return [("reference set", ["--db", VF], None)]
    sys.path.insert(0, HERE)
    import blast_db, pymysql
    from nr_parts import NrParts
    con = pymysql.connect(**blast_db.cfg(), autocommit=True)
    nr = NrParts(con.cursor())
    if which == "nr":
        return [(p[0], ["--db", p[1], "--dbsize", str(nr.letters)], nr.letters) for p in nr.dbs()]
    for p in nr.dbs():
        if p[0] == which or p[0].startswith(which + "_"):
            return [(p[0], ["--db", p[1], "--dbsize", str(nr.letters)], nr.letters)]
    sys.exit(f"--db {which}: no such part (use vf, nr, or a part name like p1)")


def search(query, which, evalue, threads, max_t=5):
    rows = []
    for label, args, _ in db_args(which):
        out = f"{OUT}/hits_{label}.tsv"
        cmd = [DIAMOND, "blastp", "--query", query, "--out", out, "--outfmt", "6", "qseqid", "sseqid", "pident",
               "evalue", "bitscore", "qlen", "length", "--max-target-seqs", str(max_t), "--evalue", str(evalue),
               "--threads", str(threads), "--quiet"] + args
        log(f"searching {label} ...")
        t = time.time()
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode:
            sys.exit(f"diamond failed on {label}: {p.stderr[-1500:]}")
        n = 0
        for line in open(out):
            f = line.rstrip("\n").split("\t")
            rows.append((label, f[0], f[1], float(f[2]), float(f[3]), float(f[4]), int(f[5]), int(f[6])))
            n += 1
        log(f"{label}: {n} hits in {time.time() - t:.0f}s")
    return rows


def flags_of(accs):
    if not accs:
        return {}
    import blast_db, pymysql
    c = pymysql.connect(**blast_db.cfg(), autocommit=True).cursor()
    c.execute("SELECT bit_value, name FROM blast_flag_bit ORDER BY bit_value")
    bits = [(int(b), n) for b, n in c.fetchall()]
    out, accs = {}, sorted(accs)
    for i in range(0, len(accs), 500):
        part = accs[i:i + 500]
        c.execute("SELECT acc, flags, protein_name, organism FROM blast_vf_ref WHERE acc IN (" +
                  ",".join(["%s"] * len(part)) + ")", part)
        for acc, fl, pname, org in c.fetchall():
            out[acc] = ([n for b, n in bits if int(fl) & b], pname, org)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, help="nucleotide FASTA of the coding sequence")
    ap.add_argument("--sites", default="u-run", choices=["u-run", "a-run", "c-run", "g-run", "gagag", "all"])
    ap.add_argument("--shift", type=int, default=1, choices=[1, -1])
    ap.add_argument("--delete", type=int, default=0, help="molecular misreading: delete n bases instead (e.g. 2)")
    ap.add_argument("--min-novel", type=int, default=MIN_NOVEL)
    ap.add_argument("--flank", type=int, default=3,
                    help="bases next to a motif or run that count as part of the site (deletions happen in or "
                         "adjacent to the motif; 3 reproduces the deposited UBB+1)")
    ap.add_argument("--db", default="vf", help="vf | nr | a part name (p1 ...) | none")
    ap.add_argument("--evalue", default="0.001")
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    OUT = a.out
    os.makedirs(OUT, exist_ok=True)

    allp, normals = [], {}
    for name, seq in read_fasta(a.inp):
        normal, ps = products(name, seq, a.sites, a.shift, a.delete, a.min_novel, a.flank)
        normals[name] = normal
        log(f"{name}: {len(seq)} nt, normal protein {len(normal)} aa, "
            f"{len(ps)} products with a foreign part of at least {a.min_novel} aa")
        for p in ps:
            log(f"  {p['how']:<22} {p['site']:<20} normal {p['head_aa']:>4} aa + foreign {p['novel_aa']:>4} aa"
                + (f" (same tail from {p['same_tail']} sites)" if p["same_tail"] > 1 else ""))
        allp += ps
    if not allp:
        log("no product above the threshold - nothing to search")
        sys.exit(0)
    write_fasta(f"{OUT}/products.faa", allp, "protein")
    write_fasta(f"{OUT}/novel.faa", allp, "novel")
    with open(f"{OUT}/products.tsv", "w") as f:
        f.write("name\thow\tsite\tnormal_aa\tnovel_aa\tsame_tail\n")
        for p in allp:
            f.write(f"{p['name']}\t{p['how']}\t{p['site']}\t{p['head_aa']}\t{p['novel_aa']}\t{p['same_tail']}\n")
    log(f"{OUT}/products.faa, novel.faa and products.tsv written")

    if a.db == "none":
        sys.exit(0)
    # the foreign part is searched, not the whole product: the front half would hit the normal protein anyway
    rows = search(f"{OUT}/novel.faa", a.db, a.evalue, a.threads)
    fl = flags_of({r[2].split("|")[1] if "|" in r[2] else r[2] for r in rows}) if a.db == "vf" else {}
    best = {}
    for label, q, s, pid, ev, bits, qlen, alen in rows:
        if q not in best or bits > best[q][5]:
            best[q] = (label, q, s, pid, ev, bits, qlen, alen)
    log("")
    log("the foreign part against " + a.db + ":")
    for p in allp:
        b = best.get(p["name"])
        if not b:
            log(f"  {p['name']:<40} {p['novel_aa']:>4} aa foreign: no hit (e-value <= {a.evalue})")
            continue
        acc = b[2].split("|")[1] if "|" in b[2] else b[2]
        info = fl.get(acc)
        extra = f"  [{', '.join(info[0])}] {info[1][:60]} ({info[2][:30]})" if info else ""
        log(f"  {p['name']:<40} {p['novel_aa']:>4} aa foreign: {b[3]:.1f} % over {b[7]} aa to {acc}, "
            f"e-value {b[4]:g}{extra}")
    log("done")
