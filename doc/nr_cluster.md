# Clustering nr: one representative per family of similar proteins

Status: concept, 2026-11-15, updated 2026-11-16. Stage 1 (90 %) measured on `p1_mammalia`; stage 2 (50 %) measured up to its last round, which runs out of memory (see *Test run*, *DIAMOND round 4 in the source*); nothing is in production.

## Goal

nr holds 1.15 billion distinct sequences, but many of them are near copies: the same protein from 500
strains of one bacterium, point variants, fragments. A search returns hundreds of hits that say the same,
and every search scans all of them. Clustering keeps **one real sequence per group (the representative)** and
a table member -> representative, as UniProt does for UniRef90/UniRef50.

- shorter hit lists: one hit per family, expanded on demand
- searches against the representatives only: smaller database, estimated 5-10x faster (not measured yet)
- a stable family key for joins (ChEMBL target -> its family in nr -> organisms)

## Why a hash does not help

nr is already non-redundant: one OID = one exact sequence, identical sequences from different organisms are
accessions of the same OID. Grouping the 64-bit hashes of `~/diamond/nr_seq_hashes.npy` (blake2b-64, state
2026-09-23) confirms it: 1 153 329 301 hashes, 1 153 329 284 distinct, **17 rows fewer** (np.sort, 29 s,
22 GB). The expected number of 64-bit collisions at this size is about 0.04. Reduction needs *similar*, not
identical sequences, so it needs alignment-based clustering.

## Method

Greedy centroid clustering with DIAMOND 2.2.8 (installed on dell, `~/.local/bin/diamond`):

| parameter | value | meaning |
|---|---|---|
| `--approx-id` | 90, then 50 | minimum sequence identity member <-> representative |
| `--member-cover` | 80 | the alignment covers >= 80 % of the member, so a fragment does not join a multi-domain protein's cluster by one domain |
| `-M` | 30G | DIAMOND memory limit; the process also runs under systemd `MemoryMax` (64 GB host) |

The representative is a real sequence of the cluster (DIAMOND picks the longest/most connected one), never a
consensus: its accessions, taxa and annotation stay valid.

**Cascade** as in UniRef:

1. `diamond linclust --approx-id 90 --member-cover 80`: linear time, finds the close variants.
2. Build a .dmnd of the stage-1 representatives only.
3. `diamond cluster --approx-id 50 --member-cover 80` on those representatives: the sensitive (slower) search
   runs on the already reduced set.

Every OID then has a `rep90` and a `rep50`. 90 % keeps orthologs of close species and strain variants apart
from each other only by a few residues, function practically identical; 50 % merges whole families, function
usually still similar.

Reduction: at 90 % **measured 2.27x on p1_mammalia** (below the 3-4x of the UniRef ratios; mammal proteomes
are diverse per species, 37 % of the sequences have no partner at 90 %). Bacterial parts (many strains of one
species) should reduce more; not measured yet. At 50 % the first three rounds of `diamond cluster` reach
**6.0x** on p1 (2.59 M representatives); the fourth (full sensitivity) did not finish, see *Test run*.

## Per part, not over the whole nr

Clustering runs on each of the six partial databases (doc/multidb_daily.md) separately:

- the parts are taxonomic (mammalia, other eukaryota, viruses/archaea, three bacterial groups); a cluster
  then never mixes a human and a bacterial protein, which is what a family view per taxon group wants
- memory and time scale with the part, not with nr (p1 15.5 M sequences to p4 352 M)
- a part can be re-clustered alone after its daily appends

The 6.35 M OIDs that are in more than one part get one representative per part they are in.

Viruses stay in: no virus is the natural source of a small-molecule drug, but viral proteins are targets of
254 ChEMBL compounds with a curated mechanism (HIV protease, SARS-CoV-2 Mpro), and some drugs are viruses
themselves (talimogene laherparepvec, AAV gene therapies).

## Schema (MariaDB, schema of blast_seq)

| table / column | content |
|---|---|
| `blast_cluster (oid, part, rep90_oid, rep50_oid, PRIMARY KEY (oid, part), KEY (rep90_oid), KEY (rep50_oid))` | membership; a representative points to itself |
| `blast_cluster_rep (rep_oid, part, level, n_members, n_taxa)` | one row per representative and level, for sorting hits by family size |
| `blast_part.rep90_dmnd`, `blast_part.rep50_dmnd` | paths of the representative databases |

Size: ~1.16 billion rows x ~17 bytes plus two secondary indexes, estimated 40-60 GB. Loaded once per
clustering with `LOAD DATA` from the DIAMOND TSV (two columns: representative, member, by accession; mapped to
OIDs through the part's `oids.npy` / record numbers).

## Search with representatives

1. Search the query against `<part>.rep50.dmnd` (or rep90 for a finer view), `--dbsize` still the letters
   of the whole nr, so e-values stay comparable with the current searches.
2. Each hit is a family; the form shows the representative with `n_members` / `n_taxa`.
3. "Expand": the members of the hit cluster (`blast_cluster WHERE rep50_oid = ?`) are built into a small
   sub-.dmnd (as `dmnd_preselect.py` does) and the query is realigned against them: exact identity per
   member, organisms from `blast_acc`.

The full search over all members stays available; representatives are an additional, faster mode.

## Daily delta

New sequences from `nr_daily_delta.py` are not clustered from scratch every day:

1. After `makedb --append`, search the new sequences of a part against its `rep90.dmnd`
   (`--approx-id 90 --member-cover 80` equivalent filter on the blastp output).
2. Hit: join that cluster (`rep90_oid`, and its `rep50_oid`). No hit: the sequence becomes a new rep90 (and
   rep50) of its own and is appended to both representative databases.
3. Monthly (or when a part grew by > 5 %): re-cluster the part fully, because greedy assignment day by day
   drifts from what a full run would produce.

## Test run

`p1_mammalia` (15 527 067 sequences, 9.25 G letters on 2026-11-15):

    systemd-run --user --unit=nrclust90 -p MemoryMax=36G -p MemorySwapMax=0 \
      diamond linclust -d /home/gh/diamond/parts/p1_mammalia.dmnd -o c90.tsv \
        --approx-id 90 --member-cover 80 -M 30G -p 16 -t /mnt/archive/nr_cluster_test

Output and log in `/mnt/archive/nr_cluster_test/`. To measure: wall time, peak memory (`/usr/bin/time -v`),
number of clusters = reduction factor, cluster size distribution; then stage 2 (50 %) on the
representatives. From these numbers the time and memory for p2-p6 are extrapolated before anything larger runs.

| measure | p1_mammalia, 90 % (`linclust`) | p1_mammalia, 50 % (`cluster`) |
|---|---|---|
| wall time | 24 min 13 s (16 threads, 1467 % CPU) | rounds 1-3: 36 min; round 4: killed after ~10 min |
| peak memory | 14.4 GB (limit 30 GB) | 36 GB = systemd MemoryMax, OOM kill in round 4 (with `-M 30G` and `-M 18G` alike) |
| clusters | 6 842 593 of 15 527 067 members | after round 3: 2 585 015 |
| reduction | 2.27x | 6.0x after round 3 |
| singletons | 5 696 727 (37 % of the sequences) | – |
| output | `c90.tsv`, 429 MB, two columns (representative, member accession) | |

Rounds of `diamond cluster --approx-id 50 --member-cover 80` (log `c50.log`, `c50_oom_M30G.log`):

| round | sensitivity | representatives after it |
|---|---|---|
| 1/4 | faster, linear | 3 022 353 |
| 2/4 | fast, linear | 2 730 918 |
| 3/4 | linclust-40, linear | 2 585 015 |
| 4/4 | default, not linear (all representatives against each other) | OOM kill in "Computing alignments" |

Three runs died at the same point at 36 GB: `-M 30G -p 16`, `-M 18G -p 16`, `-M 18G -p 8` (logs
`c50_oom_*.log`). Neither the memory limit nor the thread count bounds the alignment phase of the non-linear
round. All 2.59 M representatives (486 M letters) are one query block there (logged block size 2.55 GB,
4 index chunks). Which sub-step of "Computing alignments" takes the memory is not measured yet (see *DIAMOND
round 4 in the source*). Whatever runs clustering on dell must keep a systemd `MemoryMax` (here it protected
the host: 47 GB stayed free) and must not rely on `-M` alone.

Cluster sizes at 90 %:

| size | clusters | members |
|---|---|---|
| 1 | 5 696 727 | 5 696 727 |
| 2 | 470 819 | 941 638 |
| 3-5 | 376 119 | 1 372 919 |
| 6-20 | 225 327 | 2 237 845 |
| 21-100 | 62 519 | 2 484 557 |
| 101-1000 | 10 892 | 2 449 562 |
| > 1000 | 190 | 343 819 |

The largest clusters are barcoding genes sequenced for thousands of species (cytochrome c oxidase subunit I
14 082 members, cytochrome b 8 421 and 6 998), antibody fragments (PDB chains of Fab/VL), MHC class I and
isoforms of large proteins (kalirin, neurexin). Every record of the part (15 527 067 = `blast_part.n_seq` =
`p1_mammalia.oids.npy`, including the daily appends since 2026-11-10) is a member exactly once.

Extrapolation (linear in letters, which linclust is designed for; to be checked on a bacterial part):
p1 has 9.25 G letters of 442 G in nr, so stage 1 over all parts would take roughly 48 x 24 min = ~19 h at
the same thread count; memory stays within the limit because DIAMOND works in blocks.

## DIAMOND round 4 in the source

Read in `~/build/diamond-master-61bbbe491bed/src` (2.2.8 master 61bbbe49) and `~/build/diamond-2.2.9/src`
(release v2.2.9, 2026-11-14):

- `cluster/helpers.cpp`, `cluster_steps()`: the default steps at `--approx-id 50` are `faster_lin`,
  `fast_lin`, `linclust-40_lin`, `default`. A step ending in `_lin` is a linear round (`run_block_combo`), the
  others are all-vs-all (`run_all_vs_all`). `--cluster-steps` replaces the list; linear steps must come first.
- `cluster/search.cpp`, `configure_round()`: `config.max_target_seqs_ = 0`, i.e. no limit on targets per
  query in every round. `run_all_vs_all()`: `config.query_bins.unset()` drops any `--bin` and the block size
  comes from `-M` via `block_size()`.
- `align/align.cpp`, `align_queries()`: the seed hits loaded per bin group are bounded by `avail / 2`
  (`-M` minus the loaded sequences; 2.2.8 also capped it at `--trace-pt-fetch-size` = 10 GB). With `-M 18G` that
  is about 8.5 GB, so the hit buffer alone does not explain 36 GB.
- `-b` is rejected by `cluster` ("Option is not permitted for this workflow: b").
- v2.2.9 leaves `run_all_vs_all()` and `configure_round()` unchanged; its changes are in the linear rounds (the
  member block is loaded once and reused), the bin-group bound (cap removed) and a multipass extension
  pipeline whose option is commented out. The release notes add a `recover` workflow, but only for
  `--parallel-tmpdir`.

### Cause, measured with v2.2.9 and `--log` (2026-11-16)

v2.2.9 with the default steps (`faster_lin`, `fast_lin`, `default`: three rounds since 2.2.9), `-M 18G -p 8`,
`--log`, plus a sampler of the unit's cgroup (`c50_v229_oom_bins16.log`, `c50_v229_oom_bins16_mem.txt`):

- killed again, after 47 min 39 s, in round 3/3 (all-vs-all) at "Computing alignments... Allocating memory";
  kernel report: anon 36.8 GB, file 1.6 GB, i.e. real allocations, not page cache
- the linear rounds log `Query bins = 128`, the all-vs-all round `Query bins = 16`
- the cgroup grew from 10 GB to 36 GB during the seed search of round 3; DIAMOND's own "Current RSS" stayed
  at 3.5 GB throughout and does not reflect it

In the source (`search/hit_buffer.cpp`): `plan_bin_groups(max_bytes)` only *combines* small bins up to
`max_bytes` (from `-M`); a single bin larger than that still gets a group of its full size. `alloc_buffer()`
then maps **two** buffers of the largest group (loading + processing). Peak memory is about 2 x the largest
bin, whatever `-M` says. `run_all_vs_all()` calls `config.query_bins.unset()`, so the 128 bins of the linear
rounds drop to the sensitivity default of 16 (`search/setup.cpp`), and each bin holds 1/16 of all seed hits.

Candidate patches:

| patch | size | helps if |
|---|---|---|
| B: keep the query bins in `run_all_vs_all()`: `config.query_bins.set_if_blank(LINCLUST_QUERY_BINS)` instead of `unset()` (128 from the linear rounds, or `--bin`) | 1 line | **the measured cause**: bins 8x smaller, buffers accordingly; does not help if a few huge families fill one bin. Built on branch `query-bins-all-vs-all` of `~/build/diamond-2.2.9` (`build_bins/diamond`); test running |
| A: limit targets per query in the all-vs-all round (option instead of `max_target_seqs_ = 0`) | 3-10 lines | fewer seed hits / extensions per query in large families (COI, antibodies, olfactory receptors); costs some edges in the greedy vertex cover, as MMseqs2's `--max-seqs` does |
| C: split a bin larger than `max_bytes` when loading (or plan the bin count from the hit count) | 40-60 lines | the general fix: `-M` would then really bound this phase |

## MMseqs2 as alternative

Built on dell from `~/src/MMseqs2` (commit d401e78, 2026-09-13): `build/src/mmseqs`, release, AVX2, 32-bit
sequence IDs (`MMSEQS_INT64_IDS=0`, `src/commons/IndexTypes.h`; enough for 4.29 billion sequences per
database, nr has 1.17 billion; a k-mer table entry is 8 bytes hash + 4 bytes ID + 2 bytes position, 64-bit IDs
would add ~29 %). From the source:

- `src/workflow/Cluster.cpp`: `cluster` defaults `--max-seqs 20` (prefilter candidates per query), `-c 0.8`,
  e-value 0.001. At `--min-seq-id 0.5` the sensitivity is set to 3.0 and the cascade to 3 steps (sensitivity
  1, 2, 3) after a linclust redundancy pass. `--cov-mode 1` (coverage of the member, like DIAMOND's
  `--member-cover`) switches to greedy clustering.
- `src/linclust/kmermatcher.cpp`: linclust computes the k-mer table size in advance and splits the hash range
  so each split fits `--split-memory-limit`; the limit is enforced, not estimated.
- `data/workflow/cascaded_clustering.sh`: every step checks whether its result exists (`notExists`), so a
  killed run resumes at the failed step instead of starting again.

MMseqs2 bounds the targets per query (`--max-seqs`) where DIAMOND's all-vs-all round does not. The comparison
on p1 (same thresholds: `--min-seq-id 0.5 -c 0.8 --cov-mode 1 --split-memory-limit 30G`, input FASTA from
`diamond getseq`, database `p1db` in `/mnt/archive/nr_cluster_test/mmseqs/`) is still to be run. The
sequences of the parts are SEG-masked (X), for both tools alike.

## Public clustered databases and prior work

| resource | levels | size | format |
|---|---|---|---|
| UniRef (UniProt 2026_01) | 3, nested: UniRef90 is built from UniRef100 representatives, UniRef50 (MMseqs2) from UniRef90 representatives | UniRef100 475 217 233, UniRef90 188 848 220, UniRef50 60 315 044 clusters; UniProtKB 203 130 941 sequences | per level a FASTA of representatives (header: cluster ID, name, `n=` members, common taxon, RepID; no members) and an XML with `representativeMember` and the `member` list per entry; UniProt's `idmapping_selected.tab` (22 columns, one row per UniProtKB accession) carries UniRef100/90/50 in columns 8-10 |
| NCBI ClusteredNR | 1 (90 % identity, 90 % length, MMseqs2) | a Biostars post gives 260 861 887 sequences against 546 502 144 in nr (2.1x); whether that is the cluster count is not stated | BLAST database `nr_cluster_seq` of representatives, SQLite database representative -> members (accession, taxid, title) and scripts; downloadable since 2023-09 from the `experimental/` folder, >= 128 GB RAM, 343 GB disk; schema and update frequency not documented |
| Arcadia Science, nr clustered locally (2023-07) | 1 | 533 074 657 -> 239 387 198 sequences (-55 %), FASTA 144 -> 59 GB | `mmseqs easy-linclust --min-seq-id 0.9 -c 0.9 --cov-mode 1`; TSV membership; no runtime, RAM or search benchmark reported; superseded by ClusteredNR per the authors |

Shape: all of them ship flat files, one row per member (UniProt idmapping, ClusteredNR SQLite, MMseqs2 or
DIAMOND TSV); UniRef is a tree because each level clusters the representatives of the level below. The
planned `blast_cluster (oid, part, rep90_oid, rep50_oid)` follows the idmapping pattern with two levels; it is
a tree only if the 50 % stage runs on the 90 % representatives (MMseqs2 `cluster` and DIAMOND's cascaded
rounds do).

Prior work on search-then-expand: the UniRef paper (Suzek et al. 2015) found BLASTP against UniRef50 followed
by expanding the hit lists with cluster members advantageous compared with searching all of UniProtKB;
cfoldseeker expands Foldseek hits by joining the MMseqs2 cluster table. Linclust (Steinegger & Söding 2018)
clustered 1.6 billion metagenomic fragments to 50 % in 10 h on one server; bare linclust misses 28 % of the
pairs above the threshold, linclust + MMseqs2 cascade 2 %. DIAMOND issue #801 (deepclust, 260 GB nodes, up to
115 GB RSS) reported hangs and crashes in "Computing alignments", fixed per the maintainer in a release of
2025-01; unrelated to our measured OOM as far as known (our build is newer).

Comparison with p1: 90/80 gave 2.27x, close to ClusteredNR's 2.1x at 90/90 over all of nr; UniRef100 ->
UniRef50 is 7.9x, p1 reached 6.0x after the three linear rounds.

Sources: UniProt release notes 2026_01 (DDBJ mirror `mirror_database/uniprot/relnotes.txt`), UniRef50 README,
<https://pmc.ncbi.nlm.nih.gov/articles/PMC4375400>,
<https://ncbiinsights.ncbi.nlm.nih.gov/2023/09/26/blast-clusterednr-database-available-download/>,
<https://www.biostars.org/p/9574202/>, <https://thestacks.org/publications/resource-nr-clustering>,
<https://doi.org/10.1038/s41467-018-04964-5>, <https://github.com/bbuchfink/diamond/issues/801>.

## Sequence clusters are not function clusters (finding, no implementation planned)

Clustering by identity groups sequences, not functions. Measured example, human IgG heavy constant chains
(`blastp --very-sensitive`, 100 % coverage each):

| | IgG2 (P01859, 395 aa) | IgG3 (P01860, 446 aa) | IgG4 (P01861, 396 aa) |
|---|---|---|---|
| IgG1 (P01857, 399 aa) | 91.7 % | 84.5 % | 92.0 % |
| IgG2 | | 82.3 % | 93.2 % |
| IgG3 | | | 81.8 % |

At 50 % all four are one family. At 90 % the p1 run put them into four clusters: IgG1, IgG2, IgG4 each with 15-17
members (allotypes, other primates), IgG3 alone (long hinge). IgG3 apart is right at 90 %; IgG1/2/4 apart,
although above 90 % to each other, is a sensitivity loss of linclust, which compares each sequence with one
centroid candidate chosen by k-mers and never compares representatives with each other. Their functions differ
strongly (Fc receptor and complement activation strong for IgG1/IgG3, weak for IgG2/IgG4; Fab-arm exchange of
IgG4) through a few residues in the hinge and lower CH2 region, which barely change identity.

Consequences for the concept: a representative stands for a family, not for a function; where a few residues
decide the function, expanding to the members is required (as in *Search with representatives*); function
belongs to each member, never to the cluster.

Public function groupings (they classify by curated families, orthology or domain models, not by identity):

| resource | unit | checked |
|---|---|---|
| CATH FunFams | functional families inside a homologous superfamily, split by specificity-determining positions (GeMMA / FunFHMMer); 212 872 FunFams in CATH v4.3 | yes |
| NCBIfam / TIGRFAMs equivalogs | HMMs for proteins homologous, of the same domain architecture and consistent enough in function to share one name (enzymes: same EC); PGAP names RefSeq prokaryotic proteins (`WP_`) from them, 57 M proteins per NCBI | yes |
| eggNOG / eggnog-mapper | orthologous groups; eggnog-mapper finds the seed by DIAMOND (default) or MMseqs2 and transfers functions from the fine-grained orthologs | yes |
| UniProt idmapping | `idmapping_selected.tab`: GO in column 7, no KEGG/eggNOG columns; `idmapping.dat` holds UniProtKB cross-reference types (whether KEGG and eggNOG are among them: not checked) | partly |
| PANTHER | families and subfamilies along a tree, GO per subfamily | from memory |
| KEGG Orthology (KO) | orthologous groups by pathway function | from memory |
| Pfam / InterPro | domains and families; InterPro with GO mapping, `protein2ipr` per UniProtKB accession | from memory |
| EC numbers | enzyme function, 4 levels | from memory |
| HGNC / IMGT | gene names; IMGT keeps IGHG1-4 and their alleles apart | from memory |

None of the general groupings separates IgG1-4: KO, eggNOG and Pfam put them together; the distinction lives in
gene names (IGHG1-4) and curated annotation. Function classes are usually coarser than 90 % sequence clusters.

Sources: <https://www.ebi.ac.uk/training/online/courses/introduction-to-cath-database/functional-families-and-structural-clusters-in-cath>,
<https://pmc.ncbi.nlm.nih.gov/articles/PMC5210570>, <https://ncbi.nlm.nih.gov/genome/annotation_prok/evidence>,
<https://ncbiinsights.ncbi.nlm.nih.gov/2020/12/18/protein-family-model/>, <https://github.com/eggnogdb/eggnog-mapper>,
<https://academic.oup.com/mbe/article/38/12/5825/6379734>, UniProt idmapping README (DDBJ mirror
`mirror_database/uniprot/knowledgebase/idmapping/README`).

## Open points

- all-vs-all round of the 50 % stage on 64 GB: fewer threads did not help, `-b` is not allowed (both tested);
  cause found (16 query bins, two buffers of the largest bin, see above). Running: patch B, same setup
  (`c50_v229b.log`, `c50_v229b_mem.txt` with anon/file/disk). Alternatives: all four rounds linear
  (`--cluster-steps faster_lin fast_lin linclust-40_lin default_lin`, not measured yet) or MMseqs2

- the TSV names representative and member by accession, not by record number: map through `blast_acc.acc`
  (index `idx_acc`) to OIDs; or by rank in `<part>.oids.npy` if the record order can be recovered
- p4/p5 (352 M / 344 M sequences): within the 30 GB limit DIAMOND works in blocks; time unknown until p1 is
  measured
- form: switch "all members / one hit per family", expand button
- ChEMBL link (gerontec/ChEMBL `nr_join.py`): target -> rep50 family -> all organisms with a similar protein,
  as a fast alternative to a DIAMOND search per target
