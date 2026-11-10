# nr statistics

Generated 2026-11-10 16:25 by `pipeline/nr_parts_stats.py`. The NCBI nr is held in 6 partial DIAMOND databases (see `doc/multidb_daily.md`).

**Whole nr:** 1 172 011 885 sequences, 442 346 295 643 letters (state c3c16105517dfadcba5266ece676e985, 2026-11-10).

## Parts

| part | sequences | accessions | letters | size | status |
|---|---:|---:|---:|---:|---|
| `p1_mammalia` | 15 519 584 | 23 521 516 | 9 242 562 315 | 10.0 GB | built 2026-11-10 13:48 |
| `p2_eukaryota_other` | 156 956 422 | 211 791 761 | 79 287 658 909 | 85.8 GB | built 2026-11-10 14:33 |
| `p3_viruses_archaea_other` | 44 100 734 | 61 846 370 | 35 175 934 430 | 37.3 GB | built 2026-11-10 15:05 |
| `p4_pseudomonadota` | 352 457 752 | 674 695 503 | 117 337 044 876 | 135.2 GB | built 2026-11-10 14:46 |
| `p5_bacillati` | 344 376 088 | 632 461 469 | 114 401 679 228 | 131.4 GB | built 2026-11-10 15:55 |
| `p6_bacteria_other` | 262 759 033 | 354 295 004 | 88 107 990 412 | 99.0 GB | built 2026-11-10 15:51 |
| **sum** | **1 176 169 613** | **1 958 611 623** | **443 552 870 170** | **498.7 GB** | |

The sum is larger than the whole nr by 4 157 728 sequences: a sequence whose accessions belong to groups of different parts is stored in each of them (6 351 633 extra (sequence, group) pairs in the scan). Accessions: a part maps only the accessions of its own groups.

## Taxon groups

A sequence goes to the group of the nearest group root on its lineage (human -> mammalia, not metazoa). Counts from the scan of 2026-11-10.

| group | root taxid | part | sequences | accessions | letters |
|---|---:|---|---:|---:|---:|
| mammalia | 40674 | `p1_mammalia` | 15 519 584 | 23 521 516 | 9 242 562 315 |
| metazoa | 33208 | `p2_eukaryota_other` | 66 283 262 | 84 945 137 | 39 142 872 039 |
| fungi | 4751 | `p2_eukaryota_other` | 43 186 295 | 62 580 956 | 20 126 111 789 |
| plants | 33090 | `p2_eukaryota_other` | 38 012 261 | 51 326 735 | 15 593 004 949 |
| eukaryota | 2759 | `p2_eukaryota_other` | 9 475 541 | 12 938 933 | 4 425 878 271 |
| archaea | 2157 | `p3_viruses_archaea_other` | 27 130 037 | 37 732 086 | 7 580 711 995 |
| viruses | 10239 | `p3_viruses_archaea_other` | 13 384 407 | 19 349 224 | 26 398 072 790 |
| none | – | `p3_viruses_archaea_other` | 3 472 915 | 4 459 019 | 1 155 870 637 |
| other | – | `p3_viruses_archaea_other` | 186 997 | 306 041 | 60 143 811 |
| gammaproteobacteria | 1236 | `p4_pseudomonadota` | 181 167 146 | 392 381 439 | 62 934 442 368 |
| alphaproteobacteria | 28211 | `p4_pseudomonadota` | 96 135 065 | 159 563 600 | 29 857 321 546 |
| betaproteobacteria | 28216 | `p4_pseudomonadota` | 56 157 877 | 96 094 469 | 18 509 756 161 |
| pseudomonadota | 1224 | `p4_pseudomonadota` | 20 504 984 | 26 655 995 | 6 450 035 662 |
| actinomycetota | 201174 | `p5_bacillati` | 178 184 777 | 336 407 333 | 60 091 624 070 |
| bacillota | 1239 | `p5_bacillati` | 124 906 100 | 238 863 353 | 41 509 481 085 |
| chloroflexota | 200795 | `p5_bacillati` | 25 488 724 | 30 251 386 | 7 847 782 944 |
| cyanobacteriota | 1117 | `p5_bacillati` | 14 485 159 | 24 593 757 | 4 505 506 762 |
| mycoplasmatota | 544448 | `p5_bacillati` | 1 434 802 | 2 345 640 | 480 743 666 |
| bacteria | 2 | `p6_bacteria_other` | 129 082 477 | 160 373 860 | 41 277 511 932 |
| bacteroidota | 976 | `p6_bacteria_other` | 81 286 232 | 119 777 306 | 28 790 119 309 |
| planctomycetota | 203682 | `p6_bacteria_other` | 19 787 274 | 23 808 856 | 6 886 253 888 |
| campylobacterota | 29547 | `p6_bacteria_other` | 14 745 984 | 26 138 378 | 5 153 797 720 |
| verrucomicrobiota | 74201 | `p6_bacteria_other` | 11 294 209 | 13 686 701 | 3 854 442 985 |
| spirochaetota | 203691 | `p6_bacteria_other` | 7 051 409 | 10 509 903 | 2 296 160 671 |

`other`: in the taxonomy but under no group root; `none`: taxid unknown or 0 (e.g. PDB chains without organism).
