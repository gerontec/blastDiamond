# To do

Open after the switch from `nr_full.dmnd` to six partial databases on 2026-11-10 (`doc/multidb_daily.md`).
Companion list in the compound repository: `github.com/gerontec/ChEMBL/todo.md`.

## 1. Finish moving the parts

While `nr_full.dmnd` still existed, two parts had to be built on other disks. `/` has room since it was deleted
(514 GB free). `build/nr_parts_move.sh` copies a part, then switches `blast_part.dmnd` over under the exclusive
nr lock and deletes the old file; searches keep running on the old file until the switch.

- [ ] p4 (135 GB, from `/mnt/archive`) — copy was running when this list was written
- [ ] p6 (99 GB, from the USB SSD `/mnt/usbparts`, USB 2.0, about 35 MB/s)
- [ ] afterwards: the USB SSD only holds the rescue system and the backup again; `/mnt/usbparts` can leave
      `/etc/fstab`

## 2. Benchmark

Done 2026-11-10: `benchmark.md`, 12 single-sequence searches on `p4_pseudomonadota` (the largest part),
average 262.9 s, maximum 285.8 s, 3 GB peak memory per run.

- [ ] the measurement ran while the last part was being copied from the USB disk (load 7-9 for the first
      runs); a repeat on an idle machine would be the clean number
- [ ] a second measurement with a higher sensitivity would be useful; the form offers six levels, only `fast`
      is measured

## 3. First real run of the daily delta

`nr_daily_delta.py` was reworked for the parts (route by taxon group, one `makedb --append` per part, state per
part in `blast_part_release`). **No real run has used that code yet.** The cron entry is active again (04:30),
and daily files are pending.

- [ ] start it by hand once and check: routing, OID lists extended, `blast_release`/`blast_part_release` rows,
      the mail
- [ ] a restart after an interruption is implemented but untested

## 4. Pathogenicity flags (new on 2026-11-10)

`is_pathog` and `is_toxic` per protein, by homology to the 12 481 curated virulence factors and toxins of
Swiss-Prot. Live: `blast_vf_ref`, `blast_flag_bit` (46 categories, extends itself), `blast_pathogen_hit`
(41 619 flagged sequences from the deliveries of 2026-10-25 to 11-08), `blast_pathogen_run`, cron at 06:15
(`pathogen_scan.py --pending`), second table on `newest.php` and `api.php?r=newest_pathogen`.

- [ ] deploy the web part and the worker to the public host; the cache file `newest_pathogen.json` is written
      by the worker, so the worker has to be restarted there
- [ ] the daily cron has not fired yet (first time 2026-11-11 06:15)
- decided 2026-11-10: the public list and the API show only hits of **85 % identity or more** (4 113 of 41 619);
  weaker hits stay in `blast_pathogen_hit`, because a conserved plant enzyme reaches 52 % to a bacterial
  virulence factor without being one
- [ ] the flags of the 1.17 billion sequences already in nr are not computed; only new deliveries are scanned.
      A backfill of the whole nr would be about 70 CPU hours at the measured rate (154 315 sequences in 2 s) -
      worth it for the 13.4 million virus sequences of p3 first
- [ ] the reference set ages; UniProt releases every two months, so `vf_ref_build.py --all` needs a schedule

## 4. Smaller items

- [ ] the worker searches all six parts for a whole-nr search and lets `--taxonlist` filter inside each. With a
      taxon filter it could skip the parts that cannot contain the taxon at all (`blast_part_group`)
- [ ] drop `blast_dmnd_luecke`: the table is empty and only mattered while the record number had to be mapped to
      the OID of `nr_full`
- [ ] a future full rebuild from a new NCBI nr release should build the six parts straight from the release FASTA
      instead of from a full database
- [ ] `nr_parts.py --check` reads 9.4 GB of OID lists to prove every OID is in a part; it ran once after the
      switch and is not part of any cron
