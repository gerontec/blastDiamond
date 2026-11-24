"""Amino-acid composition of the 123 +1FS spike peptides.

A +1 frame of a codon-optimized CDS is not a random protein sequence: the
codon bias of the in-frame gene dictates which residues the shifted frame
produces. This quantifies how far the result sits from ordinary protein
composition.
"""
import collections

peps = [l.strip() for l in open('fs_peptides.txt') if l.strip()]
text = "".join(peps)
n = len(text)
c = collections.Counter(text)

print(f"{len(peps)} peptides, {n} residues, length {min(map(len,peps))}-{max(map(len,peps))}")
print()
print("aa  count   share")
for aa, k in c.most_common():
    print(f"{aa}   {k:5d}  {100*k/n:5.1f} %")
print()
top5 = c.most_common(5)
print("top 5 share:", f"{100*sum(k for _, k in top5)/n:.1f} %",
      "(" + ", ".join(a for a, _ in top5) + ")")
missing = [a for a in "ACDEFGHIKLMNPQRSTVWY" if a not in c]
print("absent residues:", "".join(missing) or "none")
rare = [(a, c.get(a, 0)) for a in "DENQKFYIVHGM"]
print("rare:", ", ".join(f"{a}={k}" for a, k in rare))
