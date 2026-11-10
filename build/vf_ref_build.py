#!/usr/bin/env python3
"""Reference proteins for the flags of new nr sequences: two manually curated sets of Swiss-Prot.

  vf        virulence factors and toxins - keyword Virulence (KW-0843) or Toxin (KW-0800), any organism
  disease   HUMAN proteins with a curated "Involvement in disease" annotation (cc_disease), which is what makes
            a new human sequence interesting: it is a variant of a protein whose variants cause disease

Reviewed only on purpose - a flag is only as good as the annotation it is transferred from, and the automatic
annotations are not a curated statement.

**What the disease set does NOT say**: the protein itself is not pathological. Swiss-Prot records that variants
of it cause a disease; the reference protein is the normal one. A hit therefore says "same protein as one whose
variants cause disease", not "this sequence is diseased".

The test the vf set has to pass: the spike of SARS-CoV-2 (P0DTC2) carries KW-0843, so it is in the set and a new
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
SETS = {
    "vf": {"query": "((keyword:KW-0843) OR (keyword:KW-0800)) AND (reviewed:true)", "extra": None},
    # every entry of this set also gets the synthetic keyword below, because its membership is the category:
    # it comes from the disease comment, not from a keyword
    "disease": {"query": "(cc_disease:*) AND (reviewed:true) AND (organism_id:9606)",
                "extra": "Disease-associated"},
}
FIELDS = "accession,id,protein_name,organism_name,organism_id,keyword,length"
BASE = "https://rest.uniprot.org/uniprotkb/stream"
T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:7.1f}s] {msg}", flush=True)


def fetch(query, fmt, out):
    import urllib.parse, urllib.request
    q = {"query": query, "format": fmt}
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
    """Both sets, then one FASTA for makedb with every accession exactly once (an entry can be in both sets)."""
    os.makedirs(VF_DIR, exist_ok=True)
    rel = None
    for name, cfg in SETS.items():
        rel = fetch(cfg["query"], "fasta", f"{VF_DIR}/vf_ref_{name}.fasta") or rel
        fetch(cfg["query"], "tsv", f"{VF_DIR}/vf_ref_{name}.tsv")
    seen, kept, dup = set(), 0, 0
    with open(f"{VF_DIR}/vf_ref.fasta", "w") as out:
        for name in SETS:
            keep = False
            for line in open(f"{VF_DIR}/vf_ref_{name}.fasta"):
                if line.startswith(">"):
                    acc = line[1:].split("|")[1] if "|" in line else line[1:].split()[0]
                    keep = acc not in seen
                    if keep:
                        seen.add(acc)
                        kept += 1
                    else:
                        dup += 1
                if keep:
                    out.write(line)
    with open(f"{VF_DIR}/vf_ref.fasta.release", "w") as f:
        f.write((rel or "?") + "\n")
    log(f"{kept} reference proteins in the merged FASTA, {dup} entries skipped as already present")
    return rel


CATEGORY_SEED = {"Virulence", "Cytolysis", "Hemolysis", "Antibiotic resistance", "Superantigen", "Vasoactive",
                 "Antimicrobial", "Allergen", "Antibiotic", "Fungicide", "Bacteriocin", "Amyloid",
                 # the disease set: its membership plus the three keywords that say something about the role
                 "Disease-associated", "Disease variant", "Proto-oncogene", "Tumor suppressor"}
DISEASE_CLASS = {"Disease-associated", "Disease variant", "Proto-oncogene", "Tumor suppressor", "Amyloid"}
MIN_REF = 1          # a category with a single reference protein still gets a bit; the count is stored with it


def is_category(kw):
    """Which UniProt keyword of an entry becomes a flag: everything that names a toxin, plus the seed list.
    Keywords about structure or method (Disulfide bond, 3D-structure, Secreted, ...) do not."""
    return "toxin" in kw.lower() or kw in CATEGORY_SEED


def classify(kw):
    k = kw.lower()
    if kw in DISEASE_CLASS:
        return "disease"
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
    source = f"Swiss-Prot vf+disease {rel}"
    con = pymysql.connect(**blast_db.cfg(), autocommit=False)
    c = con.cursor()
    import collections
    merged, counts = {}, collections.Counter()
    for name, cfg in SETS.items():
        head, n = None, 0
        for line in open(f"{VF_DIR}/vf_ref_{name}.tsv", encoding="utf-8"):
            f = line.rstrip("\n").split("\t")
            if head is None:
                head = f
                continue
            d = dict(zip(head, f))
            kws = [k.strip() for k in d.get("Keywords", "").split(";") if k.strip()]
            if cfg["extra"]:
                kws.append(cfg["extra"])          # membership of this set is itself a category
            acc = d["Entry"]
            if acc in merged:                      # in both sets: keep one row, union of the keywords
                d0, kws0 = merged[acc]
                merged[acc] = (d0, sorted(set(kws0) | set(kws)))
            else:
                merged[acc] = (d, kws)
            n += 1
        log(f"set {name}: {n} entries")
    entries = list(merged.values())
    for _, kws in entries:
        for k in kws:
            counts[k] += 1
    log(f"{len(entries)} entries after merging both sets, {len(counts)} distinct keywords")

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
    c.execute("SELECT COUNT(DISTINCT r.acc) FROM blast_vf_ref r JOIN blast_flag_bit b "
              "ON r.flags & b.bit_value WHERE b.class = 'disease'")
    log(f"disease-associated reference proteins: {c.fetchone()[0]}")
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
