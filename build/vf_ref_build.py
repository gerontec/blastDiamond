#!/usr/bin/env python3
"""Reference set for is_pathog: the manually curated virulence factors and toxins of Swiss-Prot.

Query: reviewed entries with the UniProt keyword Virulence (KW-0843) or Toxin (KW-0800). Reviewed only on
purpose - the flag of a new nr sequence is only as good as the annotation it is transferred from, and the
automatic annotations (185 158 entries without 'reviewed') are not a curated statement.

The test this set has to pass: the spike of SARS-CoV-2 (P0DTC2) carries KW-0843, so it is in the set and a new
spike sequence coming in with a daily delivery is flagged by a 100 % hit against it.

  vf_ref_build.py --download        fetch FASTA and the table from UniProt into VF_DIR
  vf_ref_build.py --load            fill blast_vf_ref from the downloaded table
  vf_ref_build.py --makedb          build vf.dmnd from the FASTA (patched diamond, --no-mask not needed here)
  vf_ref_build.py --all             all three

Env: VF_DIR (default /home/gh/diamond/vf), DIAMOND_BIN.
"""
import argparse, os, subprocess, sys, time

sys.path.insert(0, os.path.expanduser("~/python"))
import blast_db, pymysql

VF_DIR = os.environ.get("VF_DIR", "/home/gh/diamond/vf")
DIAMOND = os.environ.get("DIAMOND_BIN", os.path.expanduser("~/build/diamond-master-61bbbe491bed/build/diamond"))
QUERY = "((keyword:KW-0843) OR (keyword:KW-0800)) AND (reviewed:true)"
FIELDS = "accession,id,protein_name,organism_name,organism_id,keyword,length"
BASE = "https://rest.uniprot.org/uniprotkb/stream"
T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:7.1f}s] {msg}", flush=True)


def fetch(fmt, out):
    import urllib.parse, urllib.request
    q = {"query": QUERY, "format": fmt}
    if fmt == "tsv":
        q["fields"] = FIELDS
    url = BASE + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": "nr-pathogen-scan/1.0 (self-hosted research server)"})
    with urllib.request.urlopen(req, timeout=600) as r, open(out, "wb") as f:
        rel = r.headers.get("X-UniProt-Release", "?")
        n = 0
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
            n += len(b)
    log(f"{out}: {n} bytes, UniProt release {rel}")
    with open(out + ".release", "w") as f:
        f.write(rel + "\n")
    return rel


def download():
    os.makedirs(VF_DIR, exist_ok=True)
    rel = fetch("fasta", f"{VF_DIR}/vf_ref.fasta")
    fetch("tsv", f"{VF_DIR}/vf_ref.tsv")
    n = sum(1 for l in open(f"{VF_DIR}/vf_ref.fasta") if l.startswith(">"))
    log(f"{n} reference proteins in the FASTA")
    return rel


CATEGORY_SEED = {"Virulence", "Cytolysis", "Hemolysis", "Antibiotic resistance", "Superantigen", "Vasoactive",
                 "Antimicrobial", "Allergen", "Antibiotic", "Fungicide", "Bacteriocin", "Amyloid"}
MIN_REF = 1          # a category with a single reference protein still gets a bit; the count is stored with it


def is_category(kw):
    """Which UniProt keyword of an entry becomes a flag: everything that names a toxin, plus the seed list.
    Keywords about structure or method (Disulfide bond, 3D-structure, Secreted, ...) do not."""
    return "toxin" in kw.lower() or kw in CATEGORY_SEED


def classify(kw):
    k = kw.lower()
    if "resistance" in k:
        return "resistance"
    if "toxin" in k or kw in {"Cytolysis", "Hemolysis", "Vasoactive"}:
        return "toxin"
    return "virulence"


def ensure_bits(c, counts):
    """The bit table extends itself: a category that has no bit yet gets the next free one. Bits already given
    out keep their value for ever - a stored mask would otherwise change its meaning. Allocation order is by
    frequency, so the common categories keep the low bits."""
    c.execute("SELECT name, bit_value FROM blast_flag_bit")
    have = {n: int(b) for n, b in c.fetchall()}
    used = max(have.values()) if have else 0
    new = []
    for kw, n in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        if not is_category(kw) or n < MIN_REF:
            continue
        if kw in have:
            c.execute("UPDATE blast_flag_bit SET n_ref = %s WHERE name = %s", (n, kw))
            continue
        nxt = 1 if not used else used * 2
        if nxt > (1 << 63):
            log(f"NO BIT LEFT for '{kw}' ({n} reference proteins) - all 64 bits of flags are given out; "
                f"the column would have to become wider or categories would have to be merged")
            continue
        c.execute("INSERT INTO blast_flag_bit (bit_value, name, class, keyword, n_ref) VALUES (%s,%s,%s,%s,%s)",
                  (nxt, kw, classify(kw), kw, n))
        have[kw] = nxt
        used = nxt
        new.append((nxt, kw, n))
    for b, kw, n in new:
        log(f"  new category: bit {b} = '{kw}' ({classify(kw)}, {n} reference proteins)")
    log(f"{len(have)} categories in blast_flag_bit, {len(new)} of them new")
    return have


def load():
    rel = open(f"{VF_DIR}/vf_ref.fasta.release").read().strip()
    source = f"Swiss-Prot KW-0843/KW-0800 {rel}"
    con = pymysql.connect(**blast_db.cfg(), autocommit=False)
    c = con.cursor()
    import collections
    entries, counts = [], collections.Counter()
    head = None
    for line in open(f"{VF_DIR}/vf_ref.tsv", encoding="utf-8"):
        f = line.rstrip("\n").split("\t")
        if head is None:
            head = f
            continue
        d = dict(zip(head, f))
        kws = [k.strip() for k in d.get("Keywords", "").split(";") if k.strip()]
        for k in kws:
            counts[k] += 1
        entries.append((d, kws))
    log(f"{len(entries)} entries, {len(counts)} distinct keywords")

    bits = ensure_bits(c, counts)
    con.commit()

    rows = []
    for d, kws in entries:
        flags = 0
        for k in kws:
            flags |= bits.get(k, 0)
        rows.append((d["Entry"], d.get("Entry Name"), d.get("Protein names", "")[:255],
                     d.get("Organism", "")[:255], int(d["Organism (ID)"]) if d.get("Organism (ID)") else None,
                     flags, "; ".join(kws)[:512],
                     int(d["Length"]) if d.get("Length") else None, source))
    c.execute("DELETE FROM blast_vf_ref")
    c.executemany("INSERT INTO blast_vf_ref (acc, entry_name, protein_name, organism, taxid, flags, keywords, "
                  "len, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
    con.commit()
    c.execute("SELECT COUNT(*), SUM(flags <> 0) FROM blast_vf_ref")
    n, with_flag = c.fetchone()
    log(f"blast_vf_ref: {n} proteins, {with_flag} with at least one flag, source '{source}'")
    c.execute("""SELECT b.bit_value, b.name, b.class, COUNT(r.acc) FROM blast_flag_bit b
                 LEFT JOIN blast_vf_ref r ON r.flags & b.bit_value GROUP BY b.bit_value, b.name, b.class
                 ORDER BY b.bit_value LIMIT 8""")
    for b, name, cl, cnt in c.fetchall():
        log(f"  bit {b:>6} {name} ({cl}): {cnt} reference proteins")
    c.execute("SELECT acc, flags, protein_name FROM blast_vf_ref WHERE acc = 'P0DTC2'")
    spike = c.fetchone()
    if spike:
        c.execute("SELECT GROUP_CONCAT(name ORDER BY bit_value) FROM blast_flag_bit WHERE %s & bit_value",
                  (spike[1],))
        log(f"test, SARS-CoV-2 spike: flags {spike[1]} = {c.fetchone()[0]}")
    else:
        log("test, SARS-CoV-2 spike: NOT in the set - the set would be unusable")
    con.close()


def makedb():
    fa, db = f"{VF_DIR}/vf_ref.fasta", f"{VF_DIR}/vf"
    cmd = [DIAMOND, "makedb", "--in", fa, "--db", db, "--threads", "8"]
    log(" ".join(cmd))
    t = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        sys.exit(f"makedb failed: {p.stderr[-2000:]}")
    log(f"{db}.dmnd built in {time.time() - t:.0f}s, "
        f"{os.path.getsize(db + '.dmnd') / 1e6:.1f} MB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--load", action="store_true")
    ap.add_argument("--makedb", action="store_true")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    if a.all or a.download:
        download()
    if a.all or a.load:
        load()
    if a.all or a.makedb:
        makedb()
    if not any([a.all, a.download, a.load, a.makedb]):
        ap.print_help()
