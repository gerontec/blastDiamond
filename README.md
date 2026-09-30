# blastDiamond

NCBI nr as a DIAMOND database, its metadata in MariaDB, a JSON API on top of both, and a GPU worker
that scores protein windows for amyloid propensity on an NVIDIA Tesla P4. Everything here runs on a
single machine (Ubuntu 26.04, 62 GB RAM, 20 threads, Tesla P4).

The reference input is the full NCBI nr: **1.15 billion sequences, 1.94 billion deflines, 857 GB**
in BLAST format.

## The two DIAMOND patches

[`Diamond_append.md`](Diamond_append.md) explains both in detail. They apply to DIAMOND v2.2.8
(`5e25aca`) and are independent of each other:

* **[`diamond/0001-…`](diamond/)** — `makedb` writes the sequence offset table to disk while
  building instead of holding it in RAM. Without this the nr build is killed by the OOM killer:
  the `std::vector<SeqInfo>` reallocates at 2^30 entries and needs about 51 GB at that instant.
* **[`diamond/0002-…`](diamond/)** — `makedb --append` adds sequences to an existing database,
  with a backup of the trailer and a restore on failure. A full nr rebuild takes hours; the daily
  NCBI delta is a few hundred thousand sequences.

Both were verified byte-identical against unpatched builds on a 2.5 million sequence subset.
They are offered to the DIAMOND project under the same GPL-3.0 licence as the rest of the code.

## One pass, two targets

`build/nr_build.py` reads nr exactly once with `blastdbcmd` and splits the stream:

```
blastdbcmd -entry all -outfmt "%o\t%a\t%T\t%l\t%s\t%t"
   │
   ├─→ diamond makedb (stdin)   FASTA headers reduced to accessions:  >acc1 >acc2 …
   └─→ MariaDB                  chunks of 5 M sequences, LOAD DATA from a loader thread
```

Titles live only in MariaDB, so they are not duplicated into the `.dmnd`. Empty sequences are
rejected by `makedb` and recorded separately, so a record number in the `.dmnd` can still be mapped
back to an OID. The loader queue is bounded: if MariaDB falls behind, the stream waits instead of
filling the disk.

`build/nr_build_fix.py` is the same build with a resumable metadata side: it keeps the chunks that
`blast_import` records as fully loaded, removes the remains of the interrupted one, and verifies
the kept chunks against the stream instead of reloading them. The `.dmnd` cannot be resumed — an
interrupted `makedb` leaves no usable file — so the stream restarts at OID 0 either way, but the
450 million metadata rows are not loaded and indexed a second time. It has not yet replaced
`nr_build.py` in production.

## Keeping nr current: the append pipeline

`pipeline/` holds everything that keeps the database current after the initial build, plus the
search worker behind the API. All scripts expect to sit in one directory (they import `blast_db`
and `dmnd_getseq` from there); on the reference machine that is `~/python`.

| Script | Runs | What it does |
|--------|------|--------------|
| `diamond_append.py` | cron 03:30 | new NCBI nr release: downloads the volumes one by one (md5-checked), finds sequences none of whose accessions are known yet, appends them with `makedb --append`, writes the metadata in one transaction |
| `nr_daily_delta.py` | cron 04:30 | GenBank `daily-nc` and RefSeq `daily` files since the release: drops every protein whose sequence (blake2b-64 of the residues) is already present, appends the rest in one `--append`, records each file as a release row with its NCBI date |
| `filter_nonempty.py` | helper | drops empty sequences before `makedb` (it rejects them) |
| `blast_createdate_fill.py` | by hand | NCBI creation date (`esummary createdate`) per sequence into `blast_seq_ncbi` |
| `blast_ncbi_nachfuellen.py` | by hand | date backfill for sequences of the nr release from the GenBank daily files |
| `dmnd_getseq.py` | library/CLI | reads records straight from the `.dmnd` through its offset table |
| `dmnd_vorauswahl.py` | library/CLI | pre-selection: OIDs by taxon (with sub-taxa), length and creation date from MariaDB → small `.dmnd` |
| `nr_seq.py` | API | residues for an accession (from the `.dmnd`, masked stretches from NCBI) |
| `blast_api_worker.py` | systemd | runs the search queue: pre-selection, memory planning, DIAMOND, hits into MariaDB |
| `blast_api_key.py` | by hand | `add` / `list` / `off` API keys (stored as sha256 only) |
| `blast_db.py` | library | database connection for all of the above |

Both update scripts share the lock `~/.nr_dmnd.lock` with the worker (exclusive for appends, shared
for searches), so the `.dmnd` never changes under a running search. The daily delta waits up to 12 h
for the lock (`LOCK_WAIT_H`) when a release append is still running, and finishes a run that died
after its metadata commit by itself. Hash caches are merged block by block (about 0.8 GB of RAM,
not 47 GB).

**To rebuild it:**

1. Build DIAMOND with both patches (`diamond/`, or the upstream pull request
   https://github.com/bbuchfink/diamond/pull/991) and do the initial build (`build/`).
2. Create the tables from `db/schema.sql`; for the API also the user from `db/grants_api.sql`.
3. Database access: `BLAST_DB_INI=/path/to/file.ini` with a `[client]` section (`host`, `port`,
   `user`, `password`, `database`). Without it, `blast_db.py` reads `DB_CFG` from
   `~/mqtt-listener.py`, which is specific to the reference machine.
4. Paths are environment variables with the reference machine's values as defaults: `DMND`,
   `DIAMOND_APPEND_BIN` (the patched binary), `DIAMOND_BIN`, `TAXDIR`, `WORK`, `SEQ_CACHE`,
   `ACC_CACHE`, `NR_LOCK`, `VORAUSWAHL_DIR`, `JOB_TMP`, `JOB_THREADS`.
5. Optional mail report after each update: `MAIL_TO`, `SMTP_HOST`, `MAIL_FROM`, `MAIL_EHLO`
   (empty `MAIL_TO` = no mail).
6. Install `pipeline/crontab.example` and `pipeline/blast-api-worker.service`; put `api/api.php`
   behind a web server with its credentials in `/etc/blast_api.ini` (`db`, `user`, `pass`).

**Memory planning.** Before each search the worker reads `MemAvailable`, sets the limit to
min(48 GB, free − 4 GB), estimates the need as 0.5 GB + k · min(DB letters, block size) with k taken
from the measured peak RSS of earlier searches (`blast_ram_mess`), and picks the largest
`--block-size` of 2 / 1 / 0.5 / 0.25 / 0.1 that fits. Every search runs under `/usr/bin/time` and
adds its own measurement.

Runtimes and a worked example (daily append, date-filtered SARS-CoV-2 S1/S2 search) are in
[`Diamond_append.md`](Diamond_append.md#feature-sample-daily-append-and-a-date-filtered-search).

## Layout

| Path | What |
|------|------|
| `Diamond_append.md` | the DIAMOND patches, explained |
| `diamond/` | the two patches, `git am`-ready |
| `db/schema.sql` | live DDL, 19 tables and 4 views, no data and no grants |
| `db/grants_api.sql` | the grants of the API user, no password |
| `build/` | the one-pass nr build, the metadata loader, the status probe |
| `api/api.php` | the JSON API |
| `pipeline/` | nr updates (cron), search worker, pre-selection, direct `.dmnd` access |
| `amyloid/` | the AmyloDeep GPU worker, its job queue and the test scripts |
| `env/` | the CUDA 11.8 conda environment of the GPU worker |
| `doc/` | the full documentation as PDF, German and English, and its generator |

## The API

`api/api.php`. Metadata endpoints are free with a per-IP rate limit; searches and GPU jobs need an
`X-Api-Key`. Both long-running endpoints are queues: the POST returns a job id, a GET polls it.

| Endpoint | What |
|----------|------|
| `GET ?r=release` | which nr release is loaded, daily deltas, readiness, free memory and measured k |
| `GET ?r=acc&acc=…` | metadata for an accession, including identical entries and taxa |
| `GET ?r=taxon&taxid=…` | taxon with its lineage |
| `POST ?r=search` | DIAMOND search — `seq=…`, or `acc=…` plus optional `range=319-541`; filters `taxonlist`, `len_min`/`len_max`, `neu_tage` |
| `GET ?r=job&id=…` | search status and hits, titles and taxa joined from MariaDB |
| `POST ?r=amylo` | amyloid propensity per window on the Tesla P4 |
| `GET ?r=amylojob&id=…` | job status and the per-window scores |

Because every nr sequence is already present locally, `?r=search` accepts an accession instead of a
sequence: the residues are fetched from the local database, optionally a sub-range of them.

## AmyloDeep on the Tesla P4

`amyloid/` holds a GPU wrapper around AmyloDeep 0.3.1 (ESM-2 650M/150M, UniRep, SVM and XGBoost
ensemble) and the worker that serves the `amyl_job` queue. The models are loaded once at startup
(6.5 s) and stay resident. Results are written to MariaDB by the worker — one row per run, per
sequence, per result and per scored window — so a job submitted through the API needs no follow-up
step.

The Tesla P4 is compute capability 6.1, which CUDA 13 no longer supports; `env/` pins the working
combination (CUDA 11.8, PyTorch 2.5.1, numpy 1.26). The GPU wrapper was checked against the
original CPU implementation and agrees to 7e-7.

## Documentation

`doc/blast_diamond.pdf` (German) and `doc/blast_diamond_en.pdf` (English) are generated by
`doc/blast_diamond_pdf.py`, which queries the live database, draws the ER diagrams with Graphviz
and typesets with pdfLaTeX. The numbers in those PDFs are read at generation time, not copied in.

## Licence

The DIAMOND patches in `diamond/` are derived work of DIAMOND and carry its GPL-3.0 licence.
The scripts here are published under the same licence.
