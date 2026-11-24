"""Verify where the +1FS peptides sit in the BNT162b2 cassette.

Input: PP544446 (Vector BNT162b2 clone, 7692 bp, GenBank), whose BNT162b2 gene
feature spans 3578..7399 and whose CDS is the 2P-modified spike.

For each of the 123 predicted +1FS spike peptides, find it in the +1 reading
frame of the cassette and report the nucleotide position, so that peptides
inside the spike CDS can be told apart from peptides downstream of the spike
stop codon, i.e. in the 3' UTR.
"""
import re

CODON = {}
B = "TCAG"
AA = ("FFLLSSSSYY**CC*WLLLLPPPPHHQQRRRRIIIMTTTTNNKKSSRRVVVVAAAADDEEGGGG")
i = 0
for a in B:
    for b in B:
        for c in B:
            CODON[a + b + c] = AA[i]
            i += 1


def genbank_seq(path):
    out, on = [], False
    for line in open(path):
        if line.startswith("ORIGIN"):
            on = True
            continue
        if on:
            if line.startswith("//"):
                break
            out.append(re.sub(r"[^A-Za-z]", "", line).upper())
    return "".join(out)


def translate(nt):
    return "".join(CODON.get(nt[i:i + 3], "X") for i in range(0, len(nt) - 2, 3))


seq = genbank_seq("dl/bnt.gb")
print("cassette length:", len(seq))

GENE_START, GENE_END = 3578, 7399          # 1-based, from the record
cds = seq[GENE_START - 1:GENE_END]
prot = translate(cds)
print(f"spike CDS {GENE_END-GENE_START+1} nt -> {len(prot)} aa, "
      f"starts {prot[:12]}..., ends ...{prot[-6:]}")

peps = [l.strip() for l in open("fs_peptides.txt") if l.strip()]

# The +1 frame that matters is the one relative to the spike start codon,
# read on past the spike stop codon into the 3' UTR.
OFF = GENE_START - 1 + 1
plus1 = translate(seq[OFF:])
print(f"+1 frame from nt {OFF+1} to the cassette end: {len(plus1)} aa")

found, missing = [], []
for n, p in enumerate(peps, start=1):
    j = plus1.find(p)
    if j < 0:
        missing.append((n, p))
        continue
    nt_pos = 1 + 1 + 3 * j            # 1-based nucleotide of the first residue
    where = ("spike CDS" if GENE_START <= nt_pos <= GENE_END
             else "3' of spike CDS" if nt_pos > GENE_END
             else "5' of spike CDS")
    found.append((n, p, nt_pos, where))

print(f"\nfound in the +1 frame of the cassette: {len(found)} of {len(peps)}")
inside = [f for f in found if f[3] == "spike CDS"]
after = [f for f in found if f[3] == "3' of spike CDS"]
before = [f for f in found if f[3] == "5' of spike CDS"]
print(f"  inside spike CDS      : {len(inside)}")
print(f"  downstream (3' UTR)   : {len(after)}")
print(f"  upstream  (5' UTR)    : {len(before)}")

if after:
    print("\ndownstream peptides (nucleotide position of residue 1):")
    for n, p, pos, _ in after:
        print(f"  fs{n:03d}  nt {pos:5d}  (+{pos-GENE_END} past the CDS end)  {p}")

print(f"\nnot found in the +1 frame: {len(missing)}")
for n, p in missing[:10]:
    print(f"  fs{n:03d}  {p}")
