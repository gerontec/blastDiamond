#!/usr/bin/env python3
"""Schema for the nr parts (one-time, idempotent): size of the whole nr in blast_release, group -> part table,
part state per release. Fills blast_part_group with the cut of 2026-11-10."""
import os, sys
sys.path.insert(0, os.path.expanduser("~/python"))
import blast_db, pymysql
from nr_acc_scan import ROOTS

CUT = {
    "p1_mammalia": ["mammalia"],
    "p2_eukaryota_other": ["metazoa", "fungi", "plants", "eukaryota"],
    "p3_viruses_archaea_other": ["viruses", "archaea", "none", "other"],
    "p4_pseudomonadota": ["gammaproteobacteria", "alphaproteobacteria", "betaproteobacteria", "pseudomonadota"],
    "p5_bacillati": ["actinomycetota", "bacillota", "chloroflexota", "cyanobacteriota", "mycoplasmatota"],
    "p6_bacteria_other": ["bacteria", "bacteroidota", "planctomycetota", "campylobacterota", "verrucomicrobiota",
                          "spirochaetota"],
}

c = pymysql.connect(**blast_db.cfg(), autocommit=True).cursor()
c.execute("SHOW COLUMNS FROM blast_release LIKE 'dmnd_letters'")
if not c.fetchone():
    c.execute("ALTER TABLE blast_release ADD COLUMN dmnd_letters BIGINT UNSIGNED DEFAULT NULL "
              "COMMENT 'letters of the whole nr (all parts, each sequence once) = --dbsize' AFTER dmnd_sequenzen")
c.execute("""CREATE TABLE IF NOT EXISTS blast_part_group (
  grp VARCHAR(32) NOT NULL PRIMARY KEY COMMENT 'taxon group of nr_acc_scan.py',
  root_taxid INT UNSIGNED DEFAULT NULL COMMENT 'NULL: other (under no root) / none (taxid unknown or 0)',
  part VARCHAR(32) NOT NULL COMMENT 'blast_part.part holding the group',
  KEY idx_part (part)
) DEFAULT CHARSET=ascii COMMENT='which nr part a sequence goes to: nearest group root on its lineage'""")
c.execute("""CREATE TABLE IF NOT EXISTS blast_part_release (
  release_id INT UNSIGNED NOT NULL COMMENT 'blast_release row after which the part had this state',
  part VARCHAR(32) NOT NULL,
  n_seq BIGINT UNSIGNED NOT NULL,
  letters BIGINT UNSIGNED NOT NULL,
  dmnd_hash CHAR(32) DEFAULT NULL COMMENT 'NULL when makedb --append did not report it (restart)',
  PRIMARY KEY (release_id, part)
) DEFAULT CHARSET=ascii COMMENT='state of every nr part per update'""")
roots = dict(ROOTS)
rows = [(g, roots.get(g), p) for p, gs in CUT.items() for g in gs]
names = [r[0] for r in ROOTS] + ["other", "none"]
assert sorted(r[0] for r in rows) == sorted(names), "cut does not cover every group exactly once"
c.executemany("REPLACE INTO blast_part_group (grp, root_taxid, part) VALUES (%s,%s,%s)", rows)
c.execute("SELECT part, GROUP_CONCAT(grp ORDER BY grp) FROM blast_part_group GROUP BY part")
for r in c.fetchall():
    print(*r)
