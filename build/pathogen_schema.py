#!/usr/bin/env python3
"""Schema for the pathogenicity flags PER PROTEIN (one-time, idempotent).

A flag belongs to the sequence, not to the organism: the spike of SARS-CoV-2 is a virulence factor, its
nucleocapsid is not. It is therefore not inherited from the taxon - an organism list cannot do it (and the Human
Disease Ontology even lists taxid 2, Bacteria, as a cause of human disease, which inherited down the lineage
would mark every bacterium).

One INT column 'flags' carries all properties as a bit mask, and blast_flag_bit says what each bit means, so a
new property costs a row there instead of a column here:

    is_pathog = flags <> 0
    is_toxic  = flags & (the bits whose class is 'toxin' in blast_flag_bit)

A sequence gets its flags by homology to a manually curated reference protein (Swiss-Prot, keyword Virulence
KW-0843 or Toxin KW-0800). The row in blast_pathogen_hit keeps the evidence - which reference protein, how
identical, over how much of the sequence - instead of a bare yes.

Nothing is stored for the 1.17 billion sequences that carry no flag, and blast_acc (1.94 billion rows) is not
touched.
"""
import os, sys
sys.path.insert(0, os.path.expanduser("~/python"))
import blast_db, pymysql

c = pymysql.connect(**blast_db.cfg(), autocommit=True).cursor()

c.execute("""CREATE TABLE IF NOT EXISTS blast_flag_bit (
  bit_value BIGINT UNSIGNED NOT NULL PRIMARY KEY COMMENT 'the bit itself: 1, 2, 4, 8, ... up to bit 63',
  name VARCHAR(64) NOT NULL COMMENT 'short name of the property, taken from the source keyword',
  class VARCHAR(16) NOT NULL COMMENT 'virulence | toxin | resistance - is_toxic is the OR of the toxin bits',
  keyword VARCHAR(64) DEFAULT NULL COMMENT 'the UniProt keyword the bit is set from',
  n_ref INT UNSIGNED DEFAULT NULL COMMENT 'reference proteins carrying it at the last load',
  added TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'when this category appeared; bits are never reused',
  comment VARCHAR(255) DEFAULT NULL,
  UNIQUE KEY idx_name (name)
) DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
  COMMENT='what the bits of blast_vf_ref.flags and blast_pathogen_hit.flags mean'""")

c.execute("""CREATE TABLE IF NOT EXISTS blast_vf_ref (
  acc VARCHAR(16) NOT NULL PRIMARY KEY COMMENT 'UniProtKB accession of the reference protein',
  entry_name VARCHAR(32) DEFAULT NULL,
  protein_name VARCHAR(255) DEFAULT NULL,
  organism VARCHAR(255) DEFAULT NULL,
  taxid INT UNSIGNED DEFAULT NULL,
  flags BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'bit mask, see blast_flag_bit',
  keywords VARCHAR(512) DEFAULT NULL COMMENT 'all UniProt keywords of the entry, the evidence behind the bits',
  len SMALLINT UNSIGNED DEFAULT NULL,
  source VARCHAR(64) NOT NULL COMMENT 'which set and release it came from',
  KEY idx_taxid (taxid),
  KEY idx_flags (flags)
) DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
  COMMENT='reference proteins: manually curated virulence factors and toxins (Swiss-Prot)'""")

c.execute("""CREATE TABLE IF NOT EXISTS blast_pathogen_hit (
  oid INT UNSIGNED NOT NULL PRIMARY KEY COMMENT 'nr sequence, global OID',
  ref_acc VARCHAR(16) NOT NULL COMMENT 'best reference protein it is homologous to',
  pident FLOAT NOT NULL COMMENT 'per cent identity to that reference protein',
  qcov FLOAT DEFAULT NULL COMMENT 'per cent of the nr sequence covered by the alignment',
  evalue DOUBLE NOT NULL,
  bitscore FLOAT NOT NULL,
  flags BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'bit mask from the reference protein, see blast_flag_bit',
  found DATE DEFAULT NULL COMMENT 'NCBI date of the daily delivery the sequence came in with',
  KEY idx_ref (ref_acc),
  KEY idx_found (found),
  KEY idx_flags (flags),
  KEY idx_pident (pident)
) DEFAULT CHARSET=ascii
  COMMENT='flags per nr sequence: only flagged sequences have a row, with the evidence for it'""")

c.execute("""CREATE TABLE IF NOT EXISTS blast_pathogen_run (
  id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  datum DATE NOT NULL COMMENT 'the NCBI daily delivery that was scanned',
  started DATETIME NOT NULL,
  seconds INT UNSIGNED DEFAULT NULL,
  n_new BIGINT UNSIGNED DEFAULT NULL COMMENT 'new sequences of that delivery',
  n_query BIGINT UNSIGNED DEFAULT NULL COMMENT 'sequences actually searched',
  n_hit BIGINT UNSIGNED DEFAULT NULL COMMENT 'of them flagged',
  ref_n INT UNSIGNED DEFAULT NULL COMMENT 'size of the reference set at the time of the run',
  min_evalue VARCHAR(16) DEFAULT NULL,
  min_pident FLOAT DEFAULT NULL,
  min_qcov FLOAT DEFAULT NULL,
  note VARCHAR(255) DEFAULT NULL,
  KEY idx_datum (datum)
) DEFAULT CHARSET=ascii COMMENT='one row per daily flag scan, with its thresholds - the measurement'""")

# ---- from the first version of this schema (2026-11-10), which had one boolean column per property
for table in ("blast_vf_ref", "blast_pathogen_hit"):
    c.execute(f"SHOW COLUMNS FROM {table} LIKE 'kw_virulence'")
    if c.fetchone():
        c.execute(f"ALTER TABLE {table} DROP COLUMN kw_virulence, DROP COLUMN kw_toxin, "
                  f"ADD COLUMN flags BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'bit mask, see blast_flag_bit', "
                  f"ADD KEY idx_flags (flags)")
        print(f"{table}: kw_virulence and kw_toxin replaced by the bit mask flags")
c.execute("SHOW COLUMNS FROM blast_vf_ref LIKE 'keywords'")
if not c.fetchone():
    c.execute("ALTER TABLE blast_vf_ref ADD COLUMN keywords VARCHAR(512) DEFAULT NULL "
              "COMMENT 'all UniProt keywords of the entry, the evidence behind the bits' AFTER flags")
    print("blast_vf_ref: column keywords added")

for table in ("blast_vf_ref", "blast_pathogen_hit"):
    c.execute(f"SHOW COLUMNS FROM {table} LIKE 'flags'")
    col = c.fetchone()
    if col and "bigint" not in col[1].lower():
        c.execute(f"ALTER TABLE {table} MODIFY flags BIGINT UNSIGNED NOT NULL DEFAULT 0 "
                  f"COMMENT 'bit mask, see blast_flag_bit'")
        print(f"{table}: flags widened to 64 bit")

for t in ("blast_flag_bit", "blast_vf_ref", "blast_pathogen_hit", "blast_pathogen_run"):
    c.execute(f"SELECT COUNT(*) FROM {t}")
    print(f"{t}: {c.fetchone()[0]} rows")
