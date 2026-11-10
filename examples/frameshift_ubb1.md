# Use case: which protein comes out when the ribosome reads the frame wrong

**Question.** Has anyone worked out which proteins a human cell produces when the mRNA is read in the wrong
frame — and can we check such a product against what is already known?

Two mechanisms are documented for humans, and both give the same shape of product: a protein that is **normal at
the front and foreign behind the slip**.

| mechanism | what happens | reported product |
|---|---|---|
| **molecular misreading** (van Leeuwen et al., 1998 and later) | a dinucleotide is missing from the mRNA, in or next to a `GAGAG` motif; the frame moves for everything behind it | **UBB+1**: ubiquitin 1-75 plus a 20 aa extension. It cannot tag proteins for degradation, inhibits the proteasome at higher levels, and accumulates in the lesions of Alzheimer's disease |
| **+1 ribosomal frameshifting** (Mulroney et al., *Nature* **625**, 189-194, 2024) | N1-methylpseudouridine, the modified base of mRNA therapeutics, makes the ribosome slip at runs of that base | frameshifted products of BNT162b2, against which cellular immunity was found in mice and in humans; the paper reports no adverse outcomes and aims at future therapeutics |

Neither kind of product is in nr. nr holds proteins that somebody submitted; a mistranslation product is not
submitted. So the question has to be turned around: **compute the product, then ask the databases whether it
resembles anything known.** That is what `pipeline/frameshift_scan.py` does.

## What the tool does, and what it does not

It does **not** claim to know where a ribosome slips. It enumerates the products of every site of a chosen kind,
names the site, and searches **only the foreign part** of each product — the front half would hit the normal
protein anyway and say nothing. Nothing is written to the database; this is an analysis.

```
frameshift_scan.py --in cds.fna --sites gagag --delete 2 --flank 3   # molecular misreading
frameshift_scan.py --in cds.fna --sites u-run  --shift +1            # slip at a run of modified U
  --db vf   the 17 831 curated reference proteins (seconds), with the categories of blast_flag_bit
  --db p1   one nr part, e.g. p1_mammalia (about 20 s for a few short queries)
  --db nr   all six nr parts, E-values as in a whole-nr search (about 15 min per query)
```

## The test this had to pass

UBB+1 is the one human case with a deposited structure, so it decides whether the computation is right. Input is
nothing but the human UBB coding sequence:

```
curl -s 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=nuccore&id=NM_018955.4&rettype=fasta_cds_na&retmode=text' -o ubb_cds.fna
frameshift_scan.py --in ubb_cds.fna --sites gagag --delete 2 --flank 3 --db p1 --evalue 1
```

Measured, 2026-11-10 (690 nt, normal protein 229 aa — UBB is a polyubiquitin precursor with repeats):

```
delta2_nt216   GAGAG motif at 219   normal  74 aa + foreign  23 aa
delta2_nt218   GAGAG motif at 219   normal  74 aa + foreign  23 aa
delta2_nt219   GAGAG motif at 219   normal  74 aa + foreign  21 aa
delta2_nt224   GAGAG motif at 219   normal  75 aa + foreign  20 aa
... the same four for the second repeat at nt 447
p1_mammalia: 30 hits in 18 s
  delta2_nt224   20 aa foreign: 100.0 % over 20 aa to 3K9O_B, e-value 0.000194
  delta2_nt219   21 aa foreign: 100.0 % over 20 aa to 3K9O_B, e-value 0.000144
  delta2_nt447   21 aa foreign:  90.5 % over 21 aa to XP_045680251.1 (polyubiquitin-B, bat)
```

`3K9O_B` is in nr as *"Chain B, Ubiquitin [Homo sapiens]"* with **96 aa** — ubiquitin itself has 76. It is the
deposited UBB+1. Compared residue by residue:

```
deposited 3K9O_B (96 aa) : G MQIFVKTLTGK…VLRLRGYADLREDPDRQDHHPGSGAQ
computed  nt224  (95 aa) :   MQIFVKTLTGK…VLRLRGYADLREDPDRQDHHPGSGAQ
computed == deposited without its N-terminal G: True
```

The one extra residue is the deposited entry's N-terminal G, a remnant of the construct. The computed product is
otherwise **identical**: ubiquitin 1-75 plus the 20 aa extension. The junction G is ubiquitin's own G75, which is
why the tool counts 75 normal and 20 foreign residues rather than 76 and 19.

## Three things the test corrected

1. **The deletion sits next to the motif, not inside it.** The deposited sequence arises from removing `GT` three
   bases *behind* the `GAGAG` at nt 219 — the ΔGU case. "In or adjacent to GAGAG motifs" is to be taken
   literally, hence `--flank` (default 3). Deleting inside the motif puts **W** at the junction instead of G, and
   all four positions inside it give the same tail, because `GAGAG` minus `GA` is `GAG` wherever one cuts.
2. **A product has to be computed from the changed mRNA, read from the start.** Reading on behind the site is
   wrong: a deletion moves the frame for everything that follows. The first version did that and produced no
   product at all.
3. **FASTA identifiers end at the first blank.** Product names containing spaces were silently truncated, so a
   run reported "no hit" although DIAMOND had found seven.

## Limits

- The foreign parts are 20 to 25 aa long. DIAMOND is built for proteins, not for short peptides: the test needed
  `--evalue 1` to see the true hit at all. A short-peptide search (PSI-BLAST, HMMER) would be the right tool, and
  a hit of 20 aa is weak evidence on its own — the identity and the length are therefore printed with every hit.
- Searching the whole nr costs about 15 minutes per query (`benchmark.md`), so it belongs in a full search by
  mail, not in an ad-hoc one.
- For an mRNA therapeutic the coding sequence has to be supplied as a file; it is not in nr.
- Nothing here says that a computed product exists in a cell. It says what the frame would give, and whether
  that sequence is already known — no more.
