# What nr does not cover

The NCBI nr is every protein sequence that has been deposited publicly (GenBank translations, RefSeq,
Swiss-Prot, PDB and smaller sources), with identical sequences merged into one entry. It is a list of what
people have sequenced so far, not a census of life on Earth. Counts per part and group: `statistics.md`.

## Coverage of the living world

About 2 million species are formally described. Only part of them have protein sequences in nr, and many of
those only a handful of genes. Estimates of what exists (literature values, rounded):

| | estimated species | described |
|---|---|---|
| eukaryotes | ~8.7 million (Mora et al. 2011) | ~1/5 |
| insects | ~5.5 million | ~1 million |
| fungi | 2-4 million | ~150 000 |
| protists | largely unknown | |
| prokaryotes | millions up to ~1 trillion (Locey & Lennon 2016) | most cannot be cultured |

Metagenomic proteins (from environmental samples only) are largely not in nr; MGnify, for example, holds
several billion such proteins, far more than nr.

## Bias in the counts of this database

- **Gammaproteobacteria are the largest group (181 M sequences)** because of tens of thousands of clinical
  isolate genomes of a few pathogens (E. coli, Klebsiella, Salmonella, Pseudomonas), not because they are
  the most diverse group.
- **Mammalia (15.5 M)** are mostly human, mouse and farm animals: laboratory and medical research.
- **`bacteria` without a phylum (129 M)** is the share from metagenome-assembled genomes (MAGs): organisms
  nobody has seen, known only from their DNA. It shows how much is still undiscovered.
- **All eukaryotes together (p1 + p2, ~172 M)** are about 15 % of nr, although plants and animals make up
  almost all of Earth's biomass. Biomass and number of species have little to do with sequence counts.

In short: nr is dense around medicine, agriculture and model organisms and thin everywhere else. A search
that finds nothing in nr does not mean the protein does not exist in nature.

## Possible follow-up

Count the distinct species with sequences per group (a long query over `blast_acc`, best run at night).
