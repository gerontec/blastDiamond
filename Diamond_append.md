# `diamond makedb --append`

Two patches against DIAMOND v2.2.8 (`5e25aca`, "Fix memory leak in Array move assignment").
They are independent and can be taken separately, in this order:

| # | Patch | Files |
|---|-------|-------|
| 1 | `makedb`: write the sequence offset table to disk while building | `src/legacy/dmnd/dmnd.cpp` |
| 2 | `makedb`: add `--append` to add sequences to an existing database | `src/legacy/dmnd/dmnd.cpp`, `src/basic/config.{h,cpp}` |

Both are offered to the DIAMOND project under the same GPL-3.0 licence as the rest of the code.

## Why

We keep a local copy of NCBI nr (1.15 billion sequences, 1.94 billion deflines) as a DIAMOND
database and follow the daily NCBI deltas. Two things stood in the way.

**A full rebuild costs hours, a daily delta is a few hundred thousand sequences.** There was no way
to add sequences to an existing `.dmnd`, so every delta meant rebuilding 850 GB of input from
scratch.

**The build did not fit in memory.** `make_db()` collected one `SeqInfo` (16 bytes) per sequence in
a `std::vector` and serialized the whole vector at the end. At 2^30 entries the vector doubles its
capacity: 17.2 GB in the old block, 34.4 GB in the new one, about 51 GB live at that instant. On a
64 GB machine the build was killed by the OOM killer at 21.8 GB RSS, part way into that
reallocation.

## Patch 1 — offset table on disk

During the build the offset table is strictly append-only, and it is read back exactly once, in
order, when the trailer is written. It never needs to be in memory.

`PosSpill` replaces the `std::vector<SeqInfo>`. It buffers 2^20 entries, serializes them to
`<database>.pos_tmp` with the same `serialize()` call as before, and at the end `copy_to()` streams
that file into the database and deletes it. `discard()` deletes it if the build throws.

The on-disk format and the order of the entries are unchanged, so nothing else in DIAMOND has to
know about it. The `.pos_tmp` file grows to 16 bytes per sequence (18 GB for nr) and is removed
when the trailer has been written.

```cpp
struct PosSpill {
    static const size_t BUF = 1 << 20;
    void emplace_back(uint64_t pos, size_t len);   // buffer, flush every 2^20
    void copy_to(File& out);                        // append pos_tmp to the database, remove it
    void discard();                                 // remove pos_tmp after a failed build
};
```

The terminating entry `SeqInfo(offset, 0)` is now written directly after the spilled table instead
of being pushed onto the vector first. Same bytes, same position.

## Patch 2 — `--append`

```
diamond makedb --db nr_full.dmnd --append --in delta.faa \
    --taxonmap prot.accession2taxid.FULL.gz --taxonnodes nodes.dmp --taxonnames names.dmp
```

A `.dmnd` file is laid out as header, sequence data, offset table (`pos_array`), taxon id lists,
taxonomy nodes, taxonomy names. Everything after the sequence data is a trailer that is rewritten
on every build. Appending therefore means: cut the trailer off, keep writing sequences where the
old ones ended, then write a new trailer that merges old and new.

**Preparation** (`append_prepare`). Read and validate the two headers, then copy everything from
`header.pos_array_offset` to EOF into `<database>.append_backup`, prefixed by the three offsets
needed to put it back. Truncate the database at `pos_array_offset` and reopen it `r+b` at that
position. `letters`, `n_seqs` and `offset` continue from the header values, so accounting carries
on as if the build had never stopped.

**Trailer** (in `make_db`). The old offset table is copied back from the backup first, then
`PosSpill::copy_to()` appends the new entries, then the terminating entry. For the taxon id lists
the old block is copied back and `TaxonList::build()` is called with `n_seqs - old_seqs`, so it
only emits lists for the sequences that were actually added. Nodes and names are rebuilt as usual.

**Failure** (`append_restore`). Any exception between the truncation and the final `close()` puts
the database back: truncate to `tail_start`, write the saved header, write the saved trailer at its
old position, remove the backup, and report

```
Append failed, database restored to its previous state.
```

If `<database>.append_backup` already exists when an append starts, the run is refused — that means
an earlier append died hard (power loss, SIGKILL) and the file is the only copy of the old trailer.

**Constraints**, all checked and reported with a clear message:

* protein databases only (`--append is only supported for protein databases.`)
* the database version must be the current one
* `--taxonmap` must be given if and only if the database contains taxon id lists; likewise
  `--taxonnodes` and `--taxonnames`. Mixing them would produce a database whose trailer does not
  match its header.

## What was tested

On a 2.5 million sequence subset of nr, built two ways:

1. one `makedb` run over all 2.5 M sequences
2. `makedb` over the first 2.0 M, then `makedb --append` with the remaining 0.5 M

The resulting `.dmnd` files are **byte-identical**, including the database hash.

A full nr build with patch 1 is running as this is written: 675 million sequences in, `diamond
makedb` sits at **2.8 GB RSS**. The unpatched build died at 21.8 GB.

## Feature sample: daily append and a date-filtered search

Measured on the production database, 2026-09-30.

**Appending a daily delta.** `nr_daily_delta.py` collects the GenBank and RefSeq daily files since
the nr release, drops every protein whose sequence is already in the database (blake2b-64 hash of
the residues), and appends the rest in one `--append` run:

| Run | New sequences | Database afterwards | `makedb --append` |
|-----|---------------|---------------------|-------------------|
| 2026-09-27, 24 daily files | 2,763,461 | 1,156,092,762 sequences | 2 min 04 s |
| 2026-09-30, 6 daily files | 871,703 | 1,156,964,465 sequences (489 GB) | 1 min 47 s |

A full rebuild of the same database takes several hours.

**Searching only what is new.** Because every appended sequence keeps its NCBI creation date in
MariaDB, a search can be limited to recent entries: MariaDB selects the OIDs, the records are read
straight from `nr_full.dmnd` through its offset table, and a small `.dmnd` is built from them. The
search runs with `--dbsize` set to the letters of the full database, so E-values stay comparable
with a search against all of nr.

Query: SARS-CoV-2 spike (YP_009724390.1) split into S1 (residues 14–685, 672 aa) and S2 (686–1273,
588 aa), `--ultra-sensitive`, E-value ≤ 0.001, only sequences created within the last 100 days:

| Step | Time |
|------|------|
| Select OIDs in MariaDB (`createdate` within 100 days) | 5.5 s |
| Read 2,544,520 records (1.08 billion letters) from `nr_full.dmnd` | 27.5 s |
| `diamond makedb` of the subset | 4.3 s |
| `diamond blastp` (`--block-size 2`, 20 threads, peak RSS 1.9 GB) | 46–58 s |
| Whole job through the public API | 113.8 s |

| Query | Hits | ≥ 90 % identity | 50–90 % | < 50 % |
|-------|------|-----------------|---------|--------|
| S1 | 506 | 265 | 240 | 1 |
| S2 | 510 | 382 | 124 | 4 |

All 1,016 hits with metadata and the original subject sequences: [`examples/sars2_spike_s1s2_new100_2026-09-30.csv`](examples/sars2_spike_s1s2_new100_2026-09-30.csv) (query: [`examples/sars2_spike_s1s2_query.fa`](examples/sars2_spike_s1s2_query.fa)).

The same question as a report script, 90 days, measured 2026-10-01
([`examples/rep_newseq.py`](examples/rep_newseq.py), flow chart with the runtime of every step:
[`examples/rep_newseq_workflow.pdf`](examples/rep_newseq_workflow.pdf)):

| Step | Time |
|------|------|
| Select OIDs in MariaDB (`createdate` within 90 days → 2,543,666 sequences) | 3.5 s |
| Read 1.08 billion letters from `nr_full.dmnd` | 20.2 s |
| `diamond makedb` of the subset (cached per day afterwards) | 3.9 s |
| `diamond blastp --very-sensitive` (16 shapes, peak RSS 2.8 GB) | 13.0–13.1 s |
| `diamond blastp --ultra-sensitive` (64 shapes, peak RSS 1.9 GB) | 41–46 s |
| Metadata of the hits from MariaDB | < 0.1 s |

Found: 1,016 hits (S1 506, S2 510) on 510 distinct sequences, 4 of them not SARS-CoV-2 (infectious
bronchitis virus 2, Alphacoronavirus sp. 1, canine coronavirus 1). `--very-sensitive` and
`--ultra-sensitive` return byte-identical hits for this query, so the script defaults to the former;
the cost of `--ultra-sensitive` is the 4x larger number of seed shapes (reference histograms 18.6 s
instead of 4.8 s). DIAMOND picks its query-indexed algorithm for two short queries, so
`--index-chunks 1` has no effect. Hits: [`examples/sars2_spike_s1s2_new90_2026-10-01.csv`](examples/sars2_spike_s1s2_new90_2026-10-01.csv).

For comparison, a `--fast` search of a 51-residue query against all 1.16 billion sequences takes
895 s at `--block-size 2`.

Limitation: the date is only known for sequences that came in through the daily deltas (since
2026-09-16). The sequences of the nr release itself carry no creation date in the BLAST files;
filling it in from NCBI is a separate step.

## Upstream

Pull request: https://github.com/bbuchfink/diamond/pull/991 (both patches, rebased onto current
master). On the rebased branch, a 71,699 sequence database built in one `makedb` run and built as
57,359 + `--append` 14,340 are byte-identical, with and without taxonomy.

## Known limitations

* `--append` does not deduplicate. Appending a sequence that is already in the database gives two
  records, exactly as concatenating the FASTA inputs and rebuilding would.
* The backup file is as large as the trailer, which for nr is about 18 GB of offset table plus the
  taxon lists. It lives next to the database and is removed on success.
* Nucleotide databases are rejected rather than handled; nothing about the approach forbids them,
  they simply were not needed or tested here.

## Contact

Built and run on a single machine (Ubuntu 26.04, 62 GB RAM, 20 threads) against the full NCBI nr.
Questions: gh@heissa.de
