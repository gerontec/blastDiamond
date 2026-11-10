# DIAMOND benchmark: single query on the largest nr part

Measured 2026-11-10 18:01 by `build/nr_part_bench.py` on the database server (20 threads, 62 GB RAM, NVMe).

- database: `p4_pseudomonadota` (117.3 billion letters, 135.2 GB), the largest of the six nr parts
- search: `diamond blastp --fast`, e-value 0.001, 25 targets per query, `--block-size 2`, `--dbsize` of the whole nr (E-values as in a search of all of nr)
- 12 single-sequence queries, one diamond run each, lengths 53-2906 aa from all six parts

## Result

| | seconds |
|---|---:|
| **average** | **262.9** |
| median | 262.9 |
| **maximum** | **285.8** |
| minimum | 242.5 |

Peak memory 4.5 GB.

## Runs

| query | from part | length (aa) | seconds | hits | best identity % | peak RAM (GB) | load before |
|---|---|---:|---:|---:|---:|---:|---:|
| CAP3603586.1 | `p1_mammalia` | 53 | 242.5 | 0 | – | 3.08 | 2.78 |
| MHF0621026.1 | `p4_pseudomonadota` | 94 | 249.4 | 23 | 100 | 3.23 | 7.53 |
| WP_241988806.1 | `p5_bacillati` | 167 | 251.9 | 1 | 43.4 | 3.26 | 7.76 |
| MHD8086823.1 | `p3_viruses_archaea_other` | 281 | 258.1 | 25 | 58.7 | 3.4 | 7.32 |
| KAM5470160.1 | `p2_eukaryota_other` | 387 | 271.3 | 0 | – | 3.67 | 9.35 |
| MHE8866677.1 | `p6_bacteria_other` | 515 | 260.5 | 25 | 64.0 | 3.34 | 9.67 |
| KAN3799526.1 | `p1_mammalia` | 676 | 273.8 | 1 | 32.4 | 4.5 | 9.31 |
| WP_335261911.1 | `p4_pseudomonadota` | 842 | 248.6 | 25 | 100 | 2.96 | 9.14 |
| WP_050392819.1 | `p5_bacillati` | 1058 | 266.7 | 25 | 80.8 | 3.33 | 9.18 |
| WP_418019941.1 | `p3_viruses_archaea_other` | 1457 | 280.6 | 25 | 26.6 | 4.15 | 3.96 |
| XP_080262458.1 | `p2_eukaryota_other` | 1891 | 265.3 | 0 | – | 2.95 | 4.55 |
| WP_523699123.1 | `p6_bacteria_other` | 2906 | 285.8 | 25 | 25.7 | 4.54 | 6.02 |

A search of the whole nr runs the six parts one after another; this part holds 26 % of the letters.
