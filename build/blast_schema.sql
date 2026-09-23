CREATE TABLE IF NOT EXISTS blast_seq (
  oid     INT UNSIGNED      NOT NULL PRIMARY KEY,
  len     INT UNSIGNED      NOT NULL,
  n_acc   SMALLINT UNSIGNED NOT NULL,
  title   TEXT              NOT NULL
) ENGINE=InnoDB ROW_FORMAT=COMPRESSED KEY_BLOCK_SIZE=8 DEFAULT CHARSET=utf8mb4
  COMMENT='NCBI nr: eine Zeile je Sequenz (OID wie in der BLAST-DB), Titel der ersten Defline';

CREATE TABLE IF NOT EXISTS blast_acc (
  oid     INT UNSIGNED      NOT NULL,
  pos     SMALLINT UNSIGNED NOT NULL,
  acc     VARCHAR(32)       NOT NULL,
  taxid   INT UNSIGNED      NOT NULL,
  PRIMARY KEY (oid, pos)
) ENGINE=InnoDB ROW_FORMAT=COMPRESSED KEY_BLOCK_SIZE=8 DEFAULT CHARSET=ascii
  COMMENT='NCBI nr: eine Zeile je Defline (zusammengefasste Eintraege haben mehrere Accessions je OID)';

CREATE TABLE IF NOT EXISTS blast_taxon (
  taxid   INT UNSIGNED  NOT NULL PRIMARY KEY,
  parent  INT UNSIGNED  NOT NULL,
  `rank`  VARCHAR(32)   NOT NULL,
  name    VARCHAR(255)  NOT NULL,
  KEY idx_parent (parent),
  KEY idx_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='NCBI Taxonomie (nodes.dmp + scientific name aus names.dmp)';

CREATE TABLE IF NOT EXISTS blast_import (
  chunk_no   INT UNSIGNED    NOT NULL PRIMARY KEY,
  first_oid  INT UNSIGNED    NOT NULL,
  last_oid   INT UNSIGNED    NOT NULL,
  n_seq      INT UNSIGNED    NOT NULL,
  n_acc      INT UNSIGNED    NOT NULL,
  sekunden   DECIMAL(10,2)   NOT NULL,
  geladen    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB COMMENT='Fortschritt/Resume des nr-Metadatenimports';
