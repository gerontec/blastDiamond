# blastDiamond

## ➜ Query form: <https://blast.heissa.de/>

Paste a protein (or DNA for blastx) sequence or an nr accession, restrict by organism, length or
"published by NCBI in the last N days", press *Search now*. No account, no key. German and English.
Hits come as a sortable table and as an **Excel file**.

**This is a privately funded project. Searches that would take longer than 5 minutes are not run.**

| | limit |
|---|---|
| instant search | needs a restriction (organism and/or length and/or last N days); runs only if the predicted time fits **300 s**, otherwise it is refused with the reason before it starts |
| per address | one instant search at a time, 100 per day; 10 sequences / 20 000 letters per search |
| full search of the whole nr | by e-mail, **one per address and day**; starts after the link in the confirmation mail is opened (valid 24 h); 30 min to several hours |
| results | Excel file kept 14 days |
| all searches | one queue, strictly one search at a time on one machine; instant searches go before waiting full searches |

Always on the page: [the 60 newest human sequences in nr (longer than 10 aa)](https://blast.heissa.de/newest.php), each with
*search similar*.

Measured on this machine (2026-11-10): 100 aa against Homo sapiens (2.3 M sequences), fast: 4.3 s;
400 aa against Mammalia (15.5 M sequences, 9.2 G letters), fast: 15 s once its pre-selection is cached.
Building a pre-selection that is not cached costs about 50 µs per sequence, so large groups such as
Mammalia (13 min to build) or Bacteria (the selection alone exceeds 60 s) are refused as instant searches.

How the 300 s are guaranteed: the pre-selection runs under a MariaDB statement time limit and a sequence
cap derived from the measured build and search rates (`blast_calib`, `pipeline/diamond_calibrate.py`); then
the search time is predicted from the size of the pre-selection with a margin of 1.5, and the search is
refused instead of started if it would not fit; a hard timeout kills the whole diamond process group as the
last resort.

---

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
| `nr_daily_delta.py` | cron 04:30 | **the only regular update since 2026-10-01.** GenBank `daily-nc` and RefSeq `daily` files since the release, plus wwPDB `pdb_seqres.txt.gz` (~67 MB, protein chains) and UniProt `uniprot_sprot.fasta.gz` (~94 MB) whenever their Last-Modified date changes: drops every protein whose sequence (blake2b-64 of the residues) is already present, appends the rest in one `--append`, records each file as a release row with its date; refreshes taxdump (nodes/names) every 30 days and mails a warning when daily files expired before they were imported (RefSeq keeps them ~21 days). Flow chart: [`doc/append_workflow.pdf`](doc/append_workflow.pdf) |
| `blast_taxon_refresh.py` | cron 04:55 | reloads `blast_taxon` (names + lineage) from the taxdump that `nr_daily_delta.py` refreshes; loads into `blast_taxon_new` and swaps with one `RENAME TABLE`, so the API never sees an empty table; runs only when `nodes.dmp` is newer than the last load. Without it, organisms added after a taxdump refresh have no name/lineage |
| `rep_newOrgani.py [DAYS]` | by hand | report of organisms that entered the database in the last DAYS (default 15) and were never present before: by domain, viruses / eukaryotes / archaea in full, biggest new bacteria; NCBI E-utilities lookup for taxids missing in the taxdump (`--offline` to skip), `--tsv` for the full list. ~1-3 s for 15 days (1.17 bn sequences) |
| `diamond_append.py` | by hand only | full reconciliation against a new NCBI nr release — downloads all ~176 volumes (`nr.000` alone is 51 GB because it carries the shared LMDB files) plus `prot.accession2taxid.FULL` (26 GB); no longer in cron. New nr release: downloads the volumes one by one (md5-checked), finds sequences none of whose accessions are known yet, appends them with `makedb --append`, writes the metadata in one transaction |
| `filter_nonempty.py` | helper | drops empty sequences before `makedb` (it rejects them) |
| `blast_createdate_fill.py` | by hand | NCBI creation date (`esummary createdate`) per sequence into `blast_seq_ncbi` |
| `blast_ncbi_backfill.py` | by hand | date backfill for sequences of the nr release from the GenBank daily files |
| `dmnd_getseq.py` | library/CLI | reads records straight from the `.dmnd` through its offset table |
| `dmnd_preselect.py` | library/CLI | pre-selection: OIDs by taxon (with sub-taxa), length and creation date from MariaDB → small `.dmnd` |
| `nr_seq.py` | API | residues for an accession (from the `.dmnd`, masked stretches from NCBI) |
| `blast_api_worker.py` | systemd | runs the search queue: pre-selection, memory planning, DIAMOND, hits into MariaDB |
| `blast_api_key.py` | by hand | `add` / `list` / `off` API keys (stored as sha256 only) |
| `blast_db.py` | library | database connection for all of the above |

Both update scripts share the lock `~/.nr_dmnd.lock` with the worker (exclusive for appends, shared
for searches), so the `.dmnd` never changes under a running search. The daily delta waits up to 12 h
for the lock (`LOCK_WAIT_H`) when a manual release append is still running, and finishes a run that died
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
   `ACC_CACHE`, `NR_LOCK`, `PRESELECT_DIR`, `PRESELECT_MAX_SEQ`, `JOB_TMP`, `JOB_THREADS`.
5. Optional mail report after each update: `MAIL_TO`, `SMTP_HOST`, `MAIL_FROM`, `MAIL_EHLO`
   (empty `MAIL_TO` = no mail).
6. Install `pipeline/crontab.example` and `pipeline/blast-api-worker.service`; put `api/api.php`
   behind a web server with its credentials in `/etc/blast_api.ini` (`db`, `user`, `pass`).

**Memory planning.** Before each search the worker reads `MemAvailable`, sets the limit to
min(48 GB, free − 4 GB), estimates the need as 0.5 GB + k · min(DB letters, block size) with k taken
from the measured peak RSS of earlier searches (`blast_ram_log`), and picks the largest
`--block-size` of 2 / 1 / 0.5 / 0.25 / 0.1 that fits. Every search runs under `/usr/bin/time` and
adds its own measurement.

Runtimes and a worked example (daily append, date-filtered SARS-CoV-2 S1/S2 search) are in
[`Diamond_append.md`](Diamond_append.md#feature-sample-daily-append-and-a-date-filtered-search).

## Local database vs. NCBI online BLAST

The question "which spike sequences were added to nr in the last 90 days?" asked both ways on
2026-10-01, with the same query (S1/S2 of SARS-CoV-2 spike, [`examples/sars2_spike_s1s2_query.fa`](examples/sars2_spike_s1s2_query.fa)):

| | Local database (this repo) | NCBI online BLAST (`blastp -remote -db nr`, BLAST+ 2.17.0) |
|---|---|---|
| Restrict to the last 90 days | MariaDB `blast_seq_ncbi.createdate`: 2,543,666 sequences in 3.5 s | `-entrez_query "2026/07/03:2026/10/01[PDAT]"`: **aborted by Entrez after 13 s**, no hits; a 7-day window was aborted the same way (14 s) |
| Restrict by organism only | (not needed) | `-entrez_query "txid11118[ORGN]"` (Coronaviridae): still running after **823 s**, stopped there without a result |
| Search | `diamond blastp --very-sensitive`: **13 s** (sub-.dmnd cached), **41 s** including building it | — |
| Result | 1,016 hits on 510 sequences, all hits reported (`--max-target-seqs 0`) | none; online BLAST returns at most 5,000 targets per query, while nr holds 948,665 distinct SARS-CoV-2 sequences of spike length (1,200–1,300 aa, out of 5,428,097 SARS-CoV-2 sequences; counted in MariaDB in 9 s) |
| "New" means | new sequence content: the daily delta only appends proteins whose sequence hash is in neither nr nor an earlier delta | new record (publication date); identical sequences under a new accession look new |

The whole job through the [API](#the-api) takes about 2 minutes. For a monitoring question that is
asked every day, more than 10 minutes per online query with an uncertain outcome is the show stopper.

**When online BLAST is still the better choice.** It needs no infrastructure at all, while the local
setup needs the full nr (857 GB in BLAST format, 489 GB as `.dmnd`), a machine with about 64 GB of RAM,
and the update pipeline. For a researcher who asks a few questions, can restrict them by organism,
and has time to wait, NCBI online BLAST is a good alternative. It stops being one for date-restricted
questions over all of nr, for searches that must return every hit, and for anything that runs on a
schedule.

### Best practice: narrow first, then search fine-grained

Both the API worker and [`examples/rep_newseq.py`](examples/rep_newseq.py) use the same two steps:

1. **Narrow** the search set with cheap metadata in MariaDB: creation date, taxon (with all sub-taxa),
   sequence length. Read just those records from `nr_full.dmnd` through its offset table and build a
   small sub-database with `diamond makedb` (cached per day and nr state).
2. **Search fine-grained** in that set: a sensitive DIAMOND mode, every hit, and `--dbsize` set to the
   letters of the full nr so that E-values stay comparable with a search over all of nr.

Measure the sensitivity mode against your own question rather than defaulting to the highest. For this
query, `--very-sensitive` (16 seed shapes, 13 s) returned byte-identical hits to `--ultra-sensitive`
(64 shapes, 41–46 s). Flow chart with the runtime of every step:
[`examples/rep_newseq_workflow.pdf`](examples/rep_newseq_workflow.pdf).

## Layout

| Path | What |
|------|------|
| `Diamond_append.md` | the DIAMOND patches, explained |
| `diamond/` | the two patches, `git am`-ready |
| `db/schema.sql` | live DDL, 19 tables and 4 views, no data and no grants |
| `db/grants_api.sql` | the grants of the API user, no password |
| `build/` | the one-pass nr build, the metadata loader, the status probe |
| `api/api.php` | the JSON API |
| `web/` | the query form (PHP + Bootstrap 5.3.3 shipped locally, no CDN), German/English |
| `pipeline/` | nr updates (cron), search worker, pre-selection, direct `.dmnd` access |
| `amyloid/` | the AmyloDeep GPU worker, its job queue and the test scripts |
| `env/` | the CUDA 11.8 conda environment of the GPU worker |
| `doc/` | the full documentation as PDF, German and English, and its generator; `append_workflow.dot/.pdf` the update flow |

## The API

`api/api.php`. Metadata endpoints are free with a per-IP rate limit; searches and GPU jobs need an
`X-Api-Key`. Both long-running endpoints are queues: the POST returns a job id, a GET polls it.
Responses, field names and error messages are English since 2026-09-30; the former German input
names `neu_tage` and `von`/`bis` are still accepted as aliases of `new_days` and `from`/`to`.

| Endpoint | What |
|----------|------|
| `GET ?r=release` | which nr release is loaded, daily deltas, readiness, free memory and measured k |
| `GET ?r=acc&acc=…` | metadata for an accession, including identical entries and taxa |
| `GET ?r=taxon&taxid=…` | taxon with its lineage |
| `POST ?r=search` | DIAMOND search — `seq=…`, or `acc=…` plus optional `range=319-541`; database `db=nr|mammalia` (default `nr`; `mammalia` = part DB Mammalia, `taxonlist` must lie inside it); filters `taxonlist`, `len_min`/`len_max`, `new_days` |
| `GET ?r=job&id=…` | search status and hits, titles and taxa joined from MariaDB |
| `POST ?r=adhoc` | no key: like `search`, with a restriction, only if it fits 300 s (else status `rejected` with the reason) |
| `POST ?r=full` | no key: search of the whole nr, `email=…`, one per address and day, after confirmation |
| `GET ?r=confirm&id=…&token=…` | the confirmation link of a full search |
| `GET ?r=jobxlsx&id=…` | hits of a form search as Excel file |
| `GET ?r=taxsearch&q=…` | organism names starting with q |
| `GET ?r=newest` | the 60 Homo sapiens sequences longer than 10 aa most recently added to nr (cached hourly) |
| `POST ?r=amylo` | amyloid propensity per window on the Tesla P4 |
| `GET ?r=amylojob&id=…` | job status and the per-window scores |

Because every nr sequence is already present locally, `?r=search` accepts an accession instead of a
sequence: the residues are fetched from the local database, optionally a sub-range of them.

## Pathogenicity flags for the sequences of every daily delivery

Since 2026-11-10 every sequence that comes in with a daily NCBI delivery is checked for whether it is a known
virulence factor or toxin. The check runs **once a day on the new sequences only** - that is what makes it cheap:
154 315 sequences of one delivery took 3.7 s in total, DIAMOND itself 2 s.

**The flag belongs to the protein, not to the organism.** The spike of SARS-CoV-2 is a virulence factor, its
nucleocapsid is not, and both come from the same virus. An organism list cannot express that (and would be wrong
in the other direction too: the Human Disease Ontology lists taxid 2, *Bacteria*, as a cause of human disease,
which inherited down the lineage marks every bacterium).

| piece | what it is |
|---|---|
| reference set | 12 481 manually curated Swiss-Prot proteins with the keyword Virulence (KW-0843) or Toxin (KW-0800), `build/vf_ref_build.py` into `blast_vf_ref`, as a 4.9 MB `vf.dmnd` |
| the scan | `pipeline/pathogen_scan.py --day YYYY-MM-DD`: the new sequences are the query, the small reference database is the target; at least 50 % identity over at least 50 % of the sequence, e-value ≤ 1e-20 |
| the flags | one bit mask `flags` per sequence in `blast_pathogen_hit`, `is_pathog = flags <> 0`, `is_toxic` = the bits whose class is `toxin` |
| what the bits mean | `blast_flag_bit`, one row per category (46 at the moment: Toxin, Virulence, Neurotoxin, Ion channel impairing toxin, …). **The table extends itself**: a category that turns up in the source and has no bit yet gets the next free one, and bits already given out never change their value |
| the measurement | `blast_pathogen_run`: one row per daily scan with its thresholds, how many sequences came in and how many were flagged |
| in the form | the second table on <https://blast.heissa.de/newest.php>, and `api.php?r=newest_pathogen` - both show only hits of **85 % identity or more** |

Only flagged sequences get a row: the 1.17 billion others cost nothing, and `blast_acc` (1.94 billion rows) is
not touched by any of it.

**What the flag is worth, and what it is not.** At 85 % identity and above it is practically the same protein as
the curated one, which is why the public list starts there (4 113 of the 41 619 flagged sequences of the first
two weeks). Below that, the annotation is being transferred to a relative, which is a claim, not a measurement -
those hits are kept in the table but not shown, because a conserved enzyme of a plant reaches 52 % identity to a
bacterial virulence factor without being one. And "virulence" is the source's notion:
Swiss-Prot also annotates housekeeping proteins that a pathogen needs for an infection (enolase, GroEL,
glutamine synthetase), so they carry the flag as well.

### Frameshift products: what comes out when the ribosome slips

`pipeline/frameshift_scan.py` computes the proteins that a +1/-1 ribosomal frameshift, or a dinucleotide deletion
in the mRNA, would produce from a coding sequence, and searches the **foreign part** of each product against the
reference set, one nr part, or all of nr. It does not claim to know where a ribosome slips; it enumerates the
products of every site of a given kind and answers the question our data can answer: does the product resemble a
protein that is already known?

Checked against the one human case that is documented in a structure: given the human UBB coding sequence and a
dinucleotide deletion at a GAGAG motif, the script produces the 95 aa protein UBB+1 (ubiquitin 1-75 plus a 20 aa
extension), identical to PDB 3K9O chain B as nr holds it. Details and the open points: `todo.md`, section 5.

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
The scripts here are published under the same licence. Bootstrap in `web/assets/` is MIT licensed.
