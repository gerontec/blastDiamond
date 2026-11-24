# Are the +1 frameshift products of BNT162b2 in nr? And where do the pool peptides come from?

**Question.** Mulroney et al., *Nature* **625**, 189-194 (2024),
[10.1038/s41586-023-06800-3](https://doi.org/10.1038/s41586-023-06800-3),
report that N1-methylpseudouridine makes the ribosome slip by one base, so a modified mRNA
produces chimeric polypeptides besides the intended antigen. Their ELISpot pool (Extended Data
Table 3) lists 123 predicted +1FS spike peptides. Are any of them in nr?

**Expected answer: no** — nr holds submitted records, and nobody submits a mistranslation product.
That is the same reasoning as in [`../frameshift_ubb1.md`](../frameshift_ubb1.md). It held.

**Incidental finding, which is the point of this example:** eight of the 123 pool peptides do not
come from the spike reading frame at all. Seven lie past the spike stop codon in the vaccine's
human-derived 3' UTR, one lies upstream in the 5' UTR. Their +1 translation is therefore human
sequence, which is why one stretch matches a human GenBank entry at 88 % identity over 50 residues.

Reports: [Frameshift.pdf](https://blast.heissa.de/Frameshift.pdf) (English),
[Frameshift_de.pdf](https://blast.heissa.de/Frameshift_de.pdf) (German, longer).

## Query set

Three classes, distinguished by the FASTA id prefix in `fs_all.fa`:

| prefix | n | origin |
|---|---|---|
| `fs` | 123 | +1FS spike, predicted — Extended Data Table 3 |
| `ms` | 9 | +1FS Fluc, identified by LC-MS/MS — Extended Data Table 1 |
| `ct` | 6 | Fluc in-frame — **positive control** |

The control carries the design: firefly luciferase is in nr (`P08659`), so if its in-frame peptides
are not recovered, a negative result means nothing.

Both tables exist only as raster images, so the peptides had to be transcribed. `check_overlap.py`
validates that: the pool is built from overlapping walks, 41 transitions break (one per +1 stretch,
each ending at a stop codon) and the other 81 match letter for letter. The construct check in
`verify_utr.py` later confirmed 121 of 123 independently.

## Measured, 2026-11-24

Default DIAMOND sensitivity, as in `pipeline/frameshift_scan.py`, `--dbsize` set to the sum over all
parts. Exactness is enforced afterwards: `pident == 100` **and** `length == qlen`.

| part DB | sequences | wall clock | raw HSPs |
|---|---|---|---|
| p1_mammalia | 15.6 M | 18.1 s | 14 |
| p2_eukaryota_other | 157.3 M | 223.6 s | 323 |
| p3_viruses_archaea_other | 44.3 M | 101.2 s | 350 |
| p4_pseudomonadota | 353.9 M | 325.4 s | 156 |
| p5_bacillati | 345.4 M | 297.8 s | 180 |
| p6_bacteria_other | 263.3 M | 252.8 s | 155 |
| **total** | **1.18 bn** | **1218.9 s** | 1278 |

544 exact full-length hits — **none of them from the 123 spike peptides**. The hits are the three
recoverable control peptides and four MS peptides, which is by construction: in Fluc+1FS the
luciferase C terminus is deliberately in the +1 frame, so a slip produces genuine luciferase.

Sensitivity control: `p1_mammalia` with `--ultra-sensitive --masking 0 --comp-based-stats 0` takes
457 s instead of 18 s and gives the same result for the spike peptides.

Short-query limit, measured rather than assumed: controls `YGLNTNHR` (8 aa), `KLPIIQK` and
`IIIMDSK` (7 aa) are **not** found although they are certainly in nr. Among the spike peptides this
affects at most three; the other 120 are >= 12 aa.

## Clustering, human only

`fs_peptide_cluster.py` deliberately breaks the rule of `pipeline/nr_cluster_daily.py` ("new
sequences are compared only with each other"): these peptides are in no nr release, so the daily run
would never touch them. Counterpart restricted to *Homo sapiens* (taxid 9606, 2 285 891 sequences),
two levels at 90 % and 50 %, **82 s**.

`--cov-mode 2` instead of the daily `--cov-mode 1` — under target coverage a 15 aa peptide can never
join a 550 aa protein, so every peptide would be a singleton by construction.

Result: of 138 peptides exactly **one** shares a cluster with a human sequence (`fs116` with OID
656542024 = `AAG23172.1`, resolved below). Ten further groups are peptide-only pairs, i.e. adjacent
pool members that already overlap by ten residues — an artefact of the pool design.

Nothing is written to `blast_cluster` / `blast_cluster_rep`; those tables carry the semantics of a
daily update. Output is TSV under `results/`.

## The incidental finding

`best_hits.py` ranks the raw HSPs. Eight of 123 spike peptides have any hit at all, and the best
five are consecutive pool members all matching `AAG23172.1` (*Homo sapiens*, 103 aa, gene `PP2150`,
product "unknown"). Chained on their overlaps they give 50 contiguous residues at **44/50 = 88 %**
identity (`align_aag.py`).

`AAG23172.1` comes from `AF269289.1`, an unpublished 2000 cDNA whose ORF is an author-supplied
conceptual translation. Its coding nucleotides for that stretch are **150/150 identical** to human
*TLE5*/*AES* `NM_198969.1` from position 1216 (`test_aes.py`) — in the 3' UTR.

`verify_utr.py` then checks this against the construct itself, GenBank `PP544446` ("Vector BNT162b2
clone Vacc_Batch_FP9359", 7692 bp, spike CDS 3578..7399):

| position in the cassette | peptides |
|---|---|
| inside the spike CDS | 113 |
| **3' UTR, past the spike stop codon** | **7** (`fs116`..`fs122`, +5 to +215 nt) |
| 5' UTR, upstream of the start codon | 1 (`fs001`) |
| recovered exactly | 121 of 123 |

Mapping the 293 nt after the stop codon onto its sources:

| 3' region | source | exact match |
|---|---|---|
| nt 10-56 | *TLE5*/*AES* `NM_198969.1` from 1221 | 47 nt |
| nt 79-145 | *TLE5*/*AES* `NM_198969.1` from 1290 | 67 nt |
| nt 146-284 | mtRNR1 `NC_012920.1` from 112 | 139 nt |

So `fs116`..`fs120` are *AES*-derived, `fs121` straddles the junction and `fs122` is mitochondrial.

**Why it may matter:** a T-cell response to those pool members would be directed at a peptide of
host-derived sequence, not at a foreign product. Responses were recorded per pool, so the two cannot
be separated after the fact. The paper's full text contains no occurrence of `untranslated`, `UTR`,
`AES`, `TLE5`, `mtRNR` or `12S` — though its Methods do say the pool covers the +1 frame of "the
BNT162b2 mRNA", not of the spike CDS, so the coverage may have been deliberate.

## Two pitfalls when rebuilding this

1. Reading the GenBank `ORIGIN` block, keep **all** letters, not only `ACGTN`. Filtering IUPAC
   ambiguity codes loses five bases here (7687 instead of 7692 bp) and shifts the frame; the CDS
   then translates to `SCSWCCCLWCPA...` instead of `MFVFLVLLPLVS...`, easy to miss because it looks
   like a +1 product.
2. The relevant +1 frame is shifted against the **start codon**, not against the start of the
   vector. With the wrong reference point, none of the 123 peptides is found.

Also: `NM_005938` is *not* AES but FOXO4.

## Running it

```
check_overlap.py                                              # validate the transcription
fs_peptide_search.py  fs_all.fa out_dir     --mode fast       # ~20 min over all six parts
fs_peptide_search.py  fs_all.fa out_ultra   --mode ultra p1_mammalia
fs_peptide_cluster.py fs_all.fa cluster_out --taxid 9606 --threads 20
best_hits.py          out_dir
align_aag.py ; test_aes.py ; verify_utr.py ; composition.py
fs_report_pdf.py out_dir fs_all.fa Frameshift.pdf cluster_out --lang en
```

Credentials from `~/.my.cnf`, section `[wagodb]`. Report templates are `fs_report_{de,en}.tex`.

## Composition, for the record

Across all 123 peptides (1983 residues) S, P, T, A and C make up **64 %** of residues against about
30 % in ordinary proteomes; C 7.3 % and W 4.7 % are roughly threefold enriched, D+E fall to 2.4 %
and Y is effectively absent (3 of 1983). Low-complexity, proline- and cysteine-rich — the
unavoidable consequence of reading a GC-rich codon-optimised gene one base out of frame.
