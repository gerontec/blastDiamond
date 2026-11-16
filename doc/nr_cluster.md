# Clustering nr: one representative per family of similar proteins

Status 2026-11-16: in production are **daily clusters of the sequences each nr update adds** (MMseqs2, CPU;
see *Production*). Clustering all of nr was tested on `p1_mammalia` with DIAMOND, MMseqs2 and an own GPU proof of
concept and not pursued; those measurements are kept below as findings.

## Production: daily clusters of the new sequences (since 2026-11-16)

`pipeline/nr_cluster_daily.py` (deployed as `~/python/nr_cluster_daily.py`, cron `30 5 * * *` with nice, log
`~/python/nr_cluster_daily.log`) clusters the sequences each daily update added, **among themselves**; they are not
compared with the rest of nr.

- An update is identified by its OID end (`blast_release.dmnd_sequenzen` after `nr_daily_delta.py`); its new
  OIDs are [previous end, end). In each part they are the tail of `<part>.oids.npy`; the residues are read from the
  part's .dmnd (`dmnd_getseq.Dmnd.satz`) under a shared lock on `~/.nr_dmnd.lock` (the delta appends under the
  exclusive lock), written as FASTA with the OID as header.
- MMseqs2 (`~/src/MMseqs2/build/src/mmseqs`, d401e78, CPU), per part: `cluster --min-seq-id 0.9` over all new
  records, `createsubdb` of the representatives, `cluster --min-seq-id 0.5` over those, both with `-c 0.8
  --cov-mode 1 --max-seqs 20 --threads 16`; so every sequence has a 90 % and a 50 % representative and the two
  levels form a tree. Short sequences are kept.
- The first run took only the newest update; every later run takes all updates after the last processed one, so a
  missed day is caught up.

Tables (schema wagodb):

| table | content |
|---|---|
| `blast_cluster (oid, part, rep90_oid, rep50_oid, delta_end)`, PK `(oid, part)`, keys on both representatives and `delta_end` | one row per new sequence; a representative points to itself |
| `blast_cluster_rep (rep_oid, part, level, n_members, delta_end)`, PK `(rep_oid, part, level)` | one row per representative and level (90 / 50) |
| `blast_cluster_run (delta_end, part, n_seq, n_rep90, n_rep50, seconds, mmseqs_version, finished)` | one row per update and part |

First run, update of 2026-11-16 (OIDs 1 174 132 906-1 174 140 994, 16 s in total):

| part | new sequences | clusters 90 % | clusters 50 % |
|---|---|---|---|
| p1_mammalia | 40 | 31 | 23 |
| p2_eukaryota_other | 534 | 218 | 132 |
| p3_viruses_archaea_other | 714 | 311 | 247 |
| p4_pseudomonadota | 2 012 | 1 929 | 1 897 |
| p5_bacillati | 4 788 | 3 296 | 3 110 |
| p6_bacteria_other | 1 | 1 | 1 |

The largest 50 % clusters of that day were marker genes sequenced for phylogenies and barcoding: elongation factor
1-alpha (99 members), cytochrome c oxidase subunit I (84) and II (45). Daily updates add 8 000-84 000 sequences.

Example query, the families of the newest update:

    SELECT r.part, r.n_members, s.title FROM blast_cluster_rep r JOIN blast_seq s ON s.oid = r.rep_oid
    WHERE r.level = 50 AND r.delta_end = (SELECT MAX(delta_end) FROM blast_cluster_run) ORDER BY r.n_members DESC;

## Full clustering of nr (tested on p1_mammalia, not pursued)

The idea tested first: cluster all of nr once (1.17 billion sequences) and keep **one real sequence per group
(the representative)** with a table member -> representative, as UniProt does for UniRef90/UniRef50, for shorter
hit lists (one hit per family, expanded on demand), searches against the representatives only and a stable
family key for joins (ChEMBL target -> its family in nr -> organisms). Measured on p1_mammalia below; with
MMseqs2 the whole nr would take an estimated 4-7 days of the 64 GB host, so it was not pursued: only the daily
additions are clustered (see *Production*).

### Why a hash does not help

nr is already non-redundant: one OID = one exact sequence, identical sequences from different organisms are
accessions of the same OID. Grouping the 64-bit hashes of `~/diamond/nr_seq_hashes.npy` (blake2b-64, state
2026-09-23) confirms it: 1 153 329 301 hashes, 1 153 329 284 distinct, **17 rows fewer** (np.sort, 29 s,
22 GB). The expected number of 64-bit collisions at this size is about 0.04. Reduction needs *similar*, not
identical sequences, so it needs alignment-based clustering.

### Method

Planned as greedy centroid clustering with DIAMOND 2.2.8 (installed on dell, `~/.local/bin/diamond`):

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

### Per part, not over the whole nr

Clustering runs on each of the six partial databases (doc/multidb_daily.md) separately:

- the parts are taxonomic (mammalia, other eukaryota, viruses/archaea, three bacterial groups); a cluster
  then never mixes a human and a bacterial protein, which is what a family view per taxon group wants
- memory and time scale with the part, not with nr (p1 15.5 M sequences to p4 352 M)
- a part can be re-clustered alone after its daily appends

The 6.35 M OIDs that are in more than one part get one representative per part they are in.

Viruses stay in: no virus is the natural source of a small-molecule drug, but viral proteins are targets of
254 ChEMBL compounds with a curated mechanism (HIV protease, SARS-CoV-2 Mpro), and some drugs are viruses
themselves (talimogene laherparepvec, AAV gene therapies).

### Test run

`p1_mammalia` (15 527 067 sequences, 9.25 G letters on 2026-11-15):

    systemd-run --user --unit=nrclust90 -p MemoryMax=36G -p MemorySwapMax=0 \
      diamond linclust -d /home/gh/diamond/parts/p1_mammalia.dmnd -o c90.tsv \
        --approx-id 90 --member-cover 80 -M 30G -p 16 -t /mnt/archive/nr_cluster_test

Logs in `/mnt/archive/nr_cluster_test_keep` (the cluster files were deleted after the tests). To measure: wall time, peak memory (`/usr/bin/time -v`),
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

Rounds of `diamond cluster --approx-id 50 --member-cover 80` (logs `c50_oom_*.log`):

| round | sensitivity | representatives after it |
|---|---|---|
| 1/4 | faster, linear | 3 022 353 |
| 2/4 | fast, linear | 2 730 918 |
| 3/4 | linclust-40, linear | 2 585 015 |
| 4/4 | default, not linear (all representatives against each other) | OOM kill in "Computing alignments" |

Three runs died at the same point at 36 GB: `-M 30G -p 16`, `-M 18G -p 16`, `-M 18G -p 8` (logs
`c50_oom_*.log`). Neither the memory limit nor the thread count bounds the alignment phase of the non-linear
round. All 2.59 M representatives (486 M letters) are one query block there (logged block size 2.55 GB,
4 index chunks). The sub-step that takes the memory was measured later (see *Cause, measured with v2.2.9*). Whatever runs clustering on dell must keep a systemd `MemoryMax` (here it protected
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

### DIAMOND round 4 in the source

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

#### Cause, measured with v2.2.9 and `--log` (2026-11-16)

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
| B: keep the query bins in `run_all_vs_all()`: `config.query_bins.set_if_blank(LINCLUST_QUERY_BINS)` instead of `unset()` (128 from the linear rounds, or `--bin`) | 1 line | **the measured cause**: bins 8x smaller, buffers accordingly; does not help if a few huge families fill one bin. Built on branch `query-bins-all-vs-all` of `~/build/diamond-2.2.9` (`build_bins/diamond`), not pushed; result below |
| A: limit targets per query in the all-vs-all round (option instead of `max_target_seqs_ = 0`) | 3-10 lines | fewer seed hits / extensions per query in large families (COI, antibodies, olfactory receptors); costs some edges in the greedy vertex cover, as MMseqs2's `--max-seqs` does |
| C: split a bin larger than `max_bytes` when loading (or plan the bin count from the hit count) | 40-60 lines | the general fix: `-M` would then really bound this phase |

Test of patch B (same setup, log `c50_v229_bins128_aborted.log`): the all-vs-all round ran with `Query bins =
128`; bins were loaded in groups of 3-18 at 5.9-7.5 GB each (422-537 M trace points, `-M 18G` respected), RSS
~20 GB, no OOM. About 2.5 GB per bin, so with 16 bins one bin would have been ~20 GB and the two buffers
~40 GB, which matches the kill at 36 GB. Stopped by decision after ~45 min at bin 27 of 128: the extrapolated
duration of the round alone was over 3 h (8 threads). The patch removes the OOM as far as it ran; it was not
run to the end, so no PR has been proposed.

Where the seed hits come from: 28 % of the 6.84 M representatives at 90 % (random sample of 100 000, titles from
`blast_seq`) are immunoglobulin entries, 85 % of them "immunoglobulin heavy chain junction region" and 8 %
"light chain junction region" of *Homo sapiens* (95 %), 12-25 aa (median 17): CDR3 peptides from immune
repertoire sequencing, e.g. MBN4316372.1 `CARGGAVTAIHYFAMDVWY`, MCG02411.1 `CARALEPQNDAFDIWY`. All share the
V-gene start (`CAR`/`CVR`) and the J-gene end (`...D.WY`), so at 50 % nearly every one seeds against nearly
every other; ~1.9 M of them allow up to ~3.6e12 candidate pairs. Not measured: their share of the hits.
Excluding sequences below ~30 aa before clustering would remove them (fragments, not representatives of a
family).

### MMseqs2 as alternative

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

MMseqs2 bounds the targets per query (`--max-seqs`) where DIAMOND's all-vs-all round does not.

How `cluster` works in the source, without matrix algebra: linclust picks m k-mers per sequence (21, reduced
alphabet of 13 letters), sorts all (k-mer, sequence) pairs and makes the longest sequence of each k-mer group
the centre, so each member gets one edge, not one per pair; the cascade steps then run a k-mer prefilter
(similar k-mers from the substitution matrix, two hits on one diagonal, ungapped diagonal score, best
`--max-seqs` 20 per query), striped SIMD Smith-Waterman on those candidates and a greedy clustering of the sparse
graph (`greedyIncrementalLowMem`: an unassigned sequence becomes a representative and takes its unassigned
neighbours). "Matrix" means only the substitution matrix (20 x 20 / 13 x 13 lookup) and the per-pair DP table;
no N x N similarity matrix exists.

#### Comparison on p1_mammalia, 2026-11-16

`mmseqs cluster p1db p1clu50 tmp50 --min-seq-id 0.5 -c 0.8 --cov-mode 1 --max-seqs 20 --threads 16
--split-memory-limit 28G` under systemd `MemoryMax=36G`, nice 10; input FASTA from `diamond getseq`
(`createdb`: 13 min, 15 527 107 entries: the 40 more than on 2026-11-15 are the daily append of 2026-11-16).
Log `clu50.log` in `/mnt/archive/nr_cluster_test_keep`; the result was deleted after the tests.

| stage | time | representatives after it | reduction |
|---|---|---|---|
| linclust (redundancy pass) | ~19 min | 2 074 545 | 7.5x |
| cascade step 1 (`-s 1`): prefilter 1.4 min, align 18.9 min | ~20 min | 1 841 921 | 8.4x |
| cascade step 2 (`-s 2`): prefilter 19.1 min, align 16.2 min | ~35 min | 1 718 411 | 9.0x |
| cascade step 3 (`-s 3`): prefilter 27.5 min, align 14.4 min | ~42 min | **1 686 609** | **9.2x** |
| total | **1 h 56 min 43 s** (29.6 h CPU) | | |

Peak memory 20.5 GB RSS, 26.5 GB in the cgroup; exit 0. Part of the time overlapped with CUDA builds and conda
installs on the same host.

| same thresholds (50 %, member cover 80 %) | DIAMOND 2.2.8 / 2.2.9 | MMseqs2 d401e78 |
|---|---|---|
| after the fast stages | 2 730 918 (5.7x) after 2 linear rounds, ~36 min | 1 841 921 (8.4x) after linclust + step 1, ~40 min |
| sensitive stage | OOM at 36 GB; with patch B > 3 h extrapolated | 3 steps in ~77 min |
| result | none | **1 686 609 clusters, 9.2x, under 2 h, 20.5 GB** |

9.2x on mammals is above UniRef100 -> UniRef50 (7.9x on UniProtKB). Extrapolated to all six parts (442 G
letters, 48x p1) at roughly linear scaling: ~4 days at 16 threads; an estimate, not measured, and the bacterial
parts may reduce more and behave differently. `cluster` does not use a GPU (`--gpu` exists only for
`ungappedprefilter`, `createdb`, `databases`, `tsv2exprofiledb`).

GPU on dell (Tesla P4, Pascal sm_61, 7.5 GB), tested 2026-11-16 and removed again: MMseqs2-GPU (`libmarv`)
needs CUDA 12 and Ampere for full speed; the README rules out Pascal. A build with CUDA 11.8 for sm_61 compiled
after three adaptations (fallbacks for the CUDA 12 DPX intrinsics, `__grid_constant__` defined empty as it needs
compute_70, `thrust::cuda::par_nosync` aliased to `par` for Thrust 1.15, plus linking `libcudadevrt.a`; patch
kept as `/mnt/archive/nr_cluster_test_keep/mmseqs_cuda118_sm61.patch`). At run time the gapless prefilter kernel
asks for more than 48 KB shared memory per block (`cudaFuncSetAttribute` in `pssmkernels_gapless.cuh`), which
Pascal cannot grant (opt-in above 48 KB exists from Volta on): `CUDA error: invalid argument`. A CUDA 12 build
would fail at the same point. Making it run would need a kernel selection limited to 48 KB tiles, with doubtful
speed on a card with little FP16 throughput.

### Own GPU clustering POC against MMseqs2 (2026-11-16)

Question: does the Tesla P4 help a deliberately simple clustering, and how good is it? Code and results in
`/mnt/archive/nr_cluster_test_keep` (`kmer_gemm_cluster.py`, `poc_eval.py`, `poc_eval_100k*.txt`, `*.timing.json`).

Algorithm: every sequence becomes a 0/1 vector of its 4-mers in the Murphy 10-letter reduced alphabet (10 000
columns, not hashed); S = X @ X.T (PyTorch, in row tiles, same code on GPU and CPU) gives the shared k-mers of every
pair; score = containment of the member's k-mers in the candidate minus the candidate's chance level; per member the
64 best candidates; greedy by length as MMseqs2's greedy mode. Two lessons from the first runs: hashing to 4 096
bins let long sequences "contain" everything, and without positions (no diagonal, no alignment) a titin of
35 516 aa built from repeated domains swallowed 19 105 of 20 000 sequences; hence exact 4-mers, the chance
correction, and as a crude stand-in for locality a representative at most twice as long as the member.

100 000 random sequences of p1_mammalia (118.6 M letters), all-vs-all = 10^10 pairs, 200 TFLOP:

| | similarity (all vs all) | total |
|---|---|---|
| POC on the Tesla P4 (PyTorch 2.0.1, CUDA 11.8, fp32) | **84.7 s** (2.36 TFLOPS) | 1 min 43 s, 4.9 GB |
| POC on the CPU (i7-12700, 16 threads) | **266.4 s** (0.75 TFLOPS) | 4 min 37 s, 6.0 GB |
| MMseqs2 `cluster` (50 %, member cover 80 %, 16 threads) | - | **28.5 s**, 1.2 GB |

GPU and CPU give almost the same clusters: 0.04-1.2 % of the members get another representative (float rounding,
ties in `topk`), cluster counts differ by at most 23.

Quality against MMseqs2 on the same set (pair counting; "valid" = a sample of 2 000 member -> representative
assignments re-aligned locally with BLOSUM62, gap 11/1, reaching >= 50 % identity over the alignment columns and
>= 80 % member coverage):

| clustering | clusters | precision | recall | F1 | valid | median identity |
|---|---|---|---|---|---|---|
| **MMseqs2** | 32 300 | 1.000 | 1.000 | 1.000 | **0.955** | 0.794 |
| POC 0.05 | 9 161 | 0.060 | 0.251 | 0.097 | 0.226 | 0.350 |
| POC 0.10 | 9 570 | 0.060 | 0.251 | 0.097 | 0.211 | 0.357 |
| POC 0.15 | 12 118 | 0.062 | 0.253 | 0.100 | 0.236 | 0.360 |
| POC 0.20 | 15 894 | 0.070 | 0.265 | 0.110 | 0.286 | 0.384 |
| POC 0.25 | 20 237 | 0.080 | 0.292 | 0.126 | 0.373 | 0.469 |
| POC 0.30 | 24 650 | 0.137 | 0.340 | 0.195 | 0.549 | 0.629 |
| POC 0.40 | 33 033 | 0.443 | 0.377 | 0.407 | 0.783 | 0.780 |

At 0.40 the POC matches MMseqs2's cluster count and median identity, but only 78 % of its assignments are valid
(MMseqs2 95.5 %; the 4.5 % come from the different check: Biopython alignment, gap costs, masked X) and it finds
38 % of MMseqs2's pairs. The GPU makes the matrix product 3x faster than the CPU, but the matrix product is the
wrong approach: it computes all N^2 pairs where MMseqs2 aligns a few candidates per sequence, so MMseqs2 on the CPU
is already 3x faster than the GPU at 100 000 sequences and the gap grows with N^2 (1 M sequences: ~2.4 h on the
P4 for the similarity alone). Adding an alignment check to the POC would cost, from the measured rates
(Biopython ~27 alignments/s/thread; MMseqs2 `align` 18.9 min for up to 41 M candidate pairs at 16 threads),
minutes to hours depending on the variant; not built, decision: stay with MMseqs2 on the CPU.

## Public clustered databases and prior work

| resource | levels | size | format |
|---|---|---|---|
| UniRef (UniProt 2026_01) | 3, nested: UniRef90 is built from UniRef100 representatives, UniRef50 (MMseqs2) from UniRef90 representatives | UniRef100 475 217 233, UniRef90 188 848 220, UniRef50 60 315 044 clusters; UniProtKB 203 130 941 sequences | per level a FASTA of representatives (header: cluster ID, name, `n=` members, common taxon, RepID; no members) and an XML with `representativeMember` and the `member` list per entry; UniProt's `idmapping_selected.tab` (22 columns, one row per UniProtKB accession) carries UniRef100/90/50 in columns 8-10 |
| NCBI ClusteredNR | 1 (90 % identity, 90 % length, MMseqs2) | a Biostars post gives 260 861 887 sequences against 546 502 144 in nr (2.1x); whether that is the cluster count is not stated | BLAST database `nr_cluster_seq` of representatives, SQLite database representative -> members (accession, taxid, title) and scripts; downloadable since 2023-09 from the `experimental/` folder, >= 128 GB RAM, 343 GB disk; schema and update frequency not documented |
| Arcadia Science, nr clustered locally (2023-07) | 1 | 533 074 657 -> 239 387 198 sequences (-55 %), FASTA 144 -> 59 GB | `mmseqs easy-linclust --min-seq-id 0.9 -c 0.9 --cov-mode 1`; TSV membership; no runtime, RAM or search benchmark reported; superseded by ClusteredNR per the authors |

Shape: all of them ship flat files, one row per member (UniProt idmapping, ClusteredNR SQLite, MMseqs2 or
DIAMOND TSV); UniRef is a tree because each level clusters the representatives of the level below. The
`blast_cluster (oid, part, rep90_oid, rep50_oid, ...)` follows the idmapping pattern with two levels; it is
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
decide the function, expanding a hit family to its members is required; function
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

- daily clusters are not linked to the existing nr; a search of the daily 50 % representatives against the parts
  (DIAMOND) would attach each new family to its nearest known protein; not planned
- form: show the families of the last days (one hit per family, expand to the members); not planned
- ChEMBL link (gerontec/ChEMBL `nr_join.py`): target -> family -> organisms; would need the full clustering
- DIAMOND patch B (query bins in the all-vs-all round) removes the OOM as far as tested, not run to the end, no PR
