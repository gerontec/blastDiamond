# Six partial DIAMOND databases instead of nr_full, daily update

Status: built 2026-11-10. `nr_full.dmnd` (497 GB, 1 172 011 885 sequences) is replaced by six partial
databases derived from it and is deleted after the switch.

## Parts

The cut follows the taxon groups of `pipeline/nr_acc_scan.py` (nearest group root on the lineage wins:
human -> mammalia, not metazoa) and is stored in `blast_part_group (grp, root_taxid, part)`.

| part | groups | sequences | size |
|---|---|---|---|
| `p1_mammalia` | mammalia | 15.5 M | 10 GB |
| `p2_eukaryota_other` | metazoa, fungi, plants, eukaryota (other) | 157 M | 86 GB |
| `p3_viruses_archaea_other` | viruses, archaea, none (taxid unknown/0), other (under no root) | 44 M | ~38 GB |
| `p4_pseudomonadota` | gamma-, alpha-, betaproteobacteria, pseudomonadota (other) | 354 M | 133 GB |
| `p5_bacillati` | actinomycetota, bacillota, chloroflexota, cyanobacteriota, mycoplasmatota | 344 M | ~123 GB |
| `p6_bacteria_other` | bacteria (other), bacteroidota, planctomycetota, campylobacterota, verrucomicrobiota, spirochaetota | 263 M | ~95 GB |

Membership: a sequence (OID, possibly many accessions) belongs to every part one of its accessions' taxa
falls into. 6 351 633 OIDs are in more than one part (+0.4 % letters); every OID is in at least one.

## Keys

The OID stays the global sequence ID in MariaDB (`blast_seq`, `blast_acc`, `blast_seq_ncbi`). Each part is
built and appended in ascending OID order, so record i of a part is the OID at rank i of its sorted
`<part>.oids.npy` (uint64). `pipeline/nr_parts.py` (`NrParts.record(oid)`, `records(oids)`) finds the part by
binary search; `dmnd_preselect.py` and `nr_seq.py` read through it.

## Schema

| table / column | content |
|---|---|
| `blast_part` | one row per part: path, sequences, accessions, letters, DIAMOND hash, build times |
| `blast_part_group` | group -> part (routing of new sequences) |
| `blast_part_release` | state of every part (sequences, letters, hash) after every update |
| `blast_release.dmnd_letters` | letters of the whole nr = `--dbsize` of every search; the newest row with it is the current size, its `dmnd_hash` (`nr_parts.combined_hash`) identifies the state |

`blast_acc` / `blast_seq` stay unpartitioned: their main access is by accession and OID, which no partition
criterion prunes. A covering index `(taxid, acc)` (~50-60 GB) was rejected; taxon searches use the parts and
`diamond --taxonlist`.

## Build (one time, from nr_full)

1. `nr_acc_scan.py`: one pass over `blast_acc` in OID order (~18 min, 1.96 billion rows): taxonmap shards and
   (OID, group) pairs per chunk in `/mnt/archive/nr_split`.
2. `nr_part_build.py --scan ... --groups ...` per part: OIDs and taxonmap from the scan (duplicate DIAMOND
   keys by a 64-bit hash), records from nr_full through its offset table, piped as FASTA into
   `diamond makedb --no-mask` (no FASTA on disk). `build/nr_parts_build_all.sh` runs two parts at a time
   on different disks; the limit is reading nr_full (~500 MB/s NVMe), not CPU.
3. Verification in the build: sequence and letter counts, first, last and 5 000 random records byte for byte
   against nr_full.
4. `build/nr_parts_switch.sh`: register the size (`nr_parts.py --register`), coverage check, deploy, restart
   the worker, delete nr_full under the exclusive lock, re-enable the daily cron.

### Why `--no-mask` (diamond/0003-*.patch)

`makedb` always SEG-masks its input. The records of nr_full are masked already; masking them again turned
further residues into X (3 of 303 sampled records), which `blast_mask` cannot restore and which would make
searches differ from nr_full. The patch (against DIAMOND master 61bbbe49) adds `makedb --no-mask`. New
sequences from the daily delta are appended **with** masking, as nr_full had them.

## Daily cron (nr_daily_delta.py, 04:30)

Unchanged: download of GenBank daily-nc, RefSeq daily, PDB, Swiss-Prot; dedup by blake2b-64 against the hash
cache; new OIDs = `MAX(last_oid) + 1`; one metadata transaction.

New (`append_to_parts`):

1. Route every new sequence (one accession, one taxid) to its part: group from the freshly loaded
   `nodes.dmp` (`blast_taxon` is reloaded only at 04:55), part from `blast_part_group`.
2. Per part: FASTA + taxonmap, `makedb --append` (rollback via `.append_backup`), then `<part>.oids.npy`
   extended (new OIDs are larger than all existing ones), `blast_part` updated.
3. Restart-safe: a part whose .dmnd already holds the new records only gets its OID list written.
4. The metadata transaction writes the new size and combined hash into the new `blast_release` rows and the
   state of every part into `blast_part_release`.

The worker takes the same lock (shared) for every search, so no search sees a half-appended part.

## Search

- **Part selected in the form** (e.g. Mammalia): the part itself with `--dbsize` of the whole nr.
- **Whole nr** (full search by mail, key jobs): the six parts one after another in the one queue, same
  `--dbsize`; hits merged: a sequence found in several parts counts once (best bit score), then per query
  the best `max_target_seqs` by e-value; taxa per hit from `blast_acc` (a part only maps its own accessions).
- **Length / new-days criteria**: `dmnd_preselect.py` builds a sub-.dmnd from the parts (also `--no-mask`).
- E-values stay comparable with nr_full because `--dbsize` is the size of the whole nr.

## Open points

- move p4 (`/mnt/archive`) and p6 (USB SSD) to `/` once nr_full is gone; update `blast_part.dmnd`
- worker: search only the parts that can contain the requested taxa instead of all six with `--taxonlist`
- drop `blast_dmnd_luecke` (record number = OID only mattered for nr_full)
- a future full rebuild from a new NCBI nr release: build the six parts directly from the release FASTA
