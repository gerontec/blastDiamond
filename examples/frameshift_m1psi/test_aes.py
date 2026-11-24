"""Is the matching region of PP2150 (AF269289) the same locus as AES/TLE5?

BNT162b2 carries a 3' UTR built from the human AES/TLE5 mRNA 3' UTR plus a
mitochondrial 12S rRNA (mtRNR1) element. If a +1 frame runs past the spike stop
codon into that UTR, it translates *human* sequence -- which would explain a
near-identity to a human GenBank entry without any coincidence.

Test: take the nucleotides of AF269289 that code for the matching protein
region and look for long exact substrings in the AES mRNA and in mtRNR1.
"""
import re
import sys


def seq_of(path):
    out, on = [], False
    for line in open(path):
        if line.startswith("ORIGIN"):
            on = True
            continue
        if on:
            if line.startswith("//"):
                break
            out.append(re.sub(r"[^acgtn]", "", line.lower()))
    return "".join(out)


def fasta_of(path):
    return "".join(l.strip() for l in open(path) if not l.startswith(">")).lower()


af = seq_of("dl/af269289.gb")
print("AF269289 length:", len(af))
# CDS 557..868 (1-based); matching protein region ~ residues 37..86
cds_start = 557 - 1
lo = cds_start + 3 * 36
hi = cds_start + 3 * 86
region = af[lo:hi]
print(f"matching region nt {lo+1}..{hi} ({len(region)} nt):\n  {region}")

for name, path in [("AES/TLE5 mRNA", "dl/aes.fna"), ("mtRNR1 / 12S", "dl/mtrnr1.fna")]:
    try:
        t = fasta_of(path)
    except FileNotFoundError:
        print(f"{name}: not downloaded")
        continue
    best = 0
    where = -1
    for k in range(len(region), 11, -1):
        found = False
        for s in range(0, len(region) - k + 1):
            j = t.find(region[s:s + k])
            if j >= 0:
                best, where, found = k, j, True
                break
        if found:
            break
    print(f"{name} ({len(t)} nt): longest exact shared substring = {best} nt"
          + (f" at position {where+1}" if where >= 0 else ""))
