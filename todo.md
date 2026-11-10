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

`build/nr_part_bench.py` is armed on the server and starts by itself once p4 is moved; it writes `benchmark.md`:
12 single-sequence searches on the largest part, average and maximum runtime, peak memory per run.

- [ ] commit `benchmark.md` once it is written
- [ ] a second measurement with a higher sensitivity would be useful; the form offers six levels, only `fast`
      is measured

## 3. First real run of the daily delta

`nr_daily_delta.py` was reworked for the parts (route by taxon group, one `makedb --append` per part, state per
part in `blast_part_release`). **No real run has used that code yet.** The cron entry is active again (04:30),
and daily files are pending.

- [ ] start it by hand once and check: routing, OID lists extended, `blast_release`/`blast_part_release` rows,
      the mail
- [ ] a restart after an interruption is implemented but untested

## 4. Smaller items

- [ ] the worker searches all six parts for a whole-nr search and lets `--taxonlist` filter inside each. With a
      taxon filter it could skip the parts that cannot contain the taxon at all (`blast_part_group`)
- [ ] drop `blast_dmnd_luecke`: the table is empty and only mattered while the record number had to be mapped to
      the OID of `nr_full`
- [ ] a future full rebuild from a new NCBI nr release should build the six parts straight from the release FASTA
      instead of from a full database
- [ ] `nr_parts.py --check` reads 9.4 GB of OID lists to prove every OID is in a part; it ran once after the
      switch and is not part of any cron
