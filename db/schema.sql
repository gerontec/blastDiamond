-- DDL of the BLAST/DIAMOND + AmyloDeep schema in MariaDB.
-- Dumped from the live database on dell-3660; no data, no grants, no credentials.
-- 19 tables, 4 views.

-- Table: amyl_ergebnis
CREATE TABLE `amyl_ergebnis` (
  `lauf_id` int(11) NOT NULL,
  `seq_id` int(11) NOT NULL,
  `bewertet_von` int(11) NOT NULL COMMENT '1-basiert; >1 wenn nur die reife Kette bewertet wurde',
  `bewertet_bis` int(11) NOT NULL,
  `avg_prob` float NOT NULL COMMENT 'Mittel ueber alle Fenster',
  `max_prob` float NOT NULL,
  `max_pos` int(11) NOT NULL COMMENT '1-basiert, Beginn des staerksten Fensters',
  `sekunden` float DEFAULT NULL,
  PRIMARY KEY (`lauf_id`,`seq_id`),
  KEY `fk_amyl_erg_seq` (`seq_id`),
  CONSTRAINT `fk_amyl_erg_lauf` FOREIGN KEY (`lauf_id`) REFERENCES `amyl_lauf` (`lauf_id`),
  CONSTRAINT `fk_amyl_erg_seq` FOREIGN KEY (`seq_id`) REFERENCES `amyl_sequenz` (`seq_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='AmyloDeep je Lauf und Sequenz';

-- Table: amyl_fenster
CREATE TABLE `amyl_fenster` (
  `lauf_id` int(11) NOT NULL,
  `seq_id` int(11) NOT NULL,
  `pos` int(11) NOT NULL COMMENT '1-basiert, bezogen auf die ganze Sequenz',
  `prob` float NOT NULL COMMENT 'Ensemble-Wahrscheinlichkeit amyloidogen',
  PRIMARY KEY (`lauf_id`,`seq_id`,`pos`),
  CONSTRAINT `fk_amyl_fenster_erg` FOREIGN KEY (`lauf_id`, `seq_id`) REFERENCES `amyl_ergebnis` (`lauf_id`, `seq_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='AmyloDeep je Fenster; Fenstertext = SUBSTRING(sequenz, pos, window_size)';

-- Table: amyl_job
CREATE TABLE `amyl_job` (
  `job_id` char(24) NOT NULL COMMENT '96 bit Zufall, zugleich Leseberechtigung',
  `key_id` int(11) NOT NULL COMMENT 'blast_api_key.key_id (gemeinsame Schluessel mit der DIAMOND-API)',
  `status` enum('queued','running','done','failed') NOT NULL DEFAULT 'queued',
  `acc` varchar(30) DEFAULT NULL COMMENT 'Accession aus nr; entweder acc oder query',
  `bereich_von` int(11) DEFAULT NULL,
  `bereich_bis` int(11) DEFAULT NULL COMMENT 'Teilbereich der Sequenz, 1-basiert',
  `query` mediumtext DEFAULT NULL COMMENT 'uebergebene Sequenz (nur wenn keine Accession)',
  `window_size` int(11) NOT NULL DEFAULT 10,
  `erstellt` datetime NOT NULL DEFAULT current_timestamp(),
  `gestartet` datetime DEFAULT NULL,
  `fertig` datetime DEFAULT NULL,
  `sekunden` float DEFAULT NULL,
  `lauf_id` int(11) DEFAULT NULL COMMENT 'amyl_lauf des Worker-Laufs',
  `seq_id` int(11) DEFAULT NULL COMMENT 'amyl_sequenz der bewerteten Sequenz',
  `error` text DEFAULT NULL,
  PRIMARY KEY (`job_id`),
  KEY `k_status` (`status`,`erstellt`),
  KEY `k_key` (`key_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='Warteschlange der AmyloDeep-API (Worker amylo_api_worker.py)';

-- Table: amyl_kandidat
CREATE TABLE `amyl_kandidat` (
  `kandidat_id` int(11) NOT NULL AUTO_INCREMENT,
  `kurz` varchar(16) DEFAULT NULL COMMENT 'Amyloid-Kuerzel (ATTR, Abeta, ...); NULL bei Einzelabfragen',
  `name` varchar(160) DEFAULT NULL COMMENT 'Proteinname laut Kandidatenliste bzw. FASTA-Kennung',
  `acc_basis` varchar(24) DEFAULT NULL COMMENT 'Accession ohne Version (P02766); Sequenz kommt aus nr',
  `rolle` enum('amyloid','kontrolle','einzel') NOT NULL DEFAULT 'amyloid',
  `reif_von` int(11) DEFAULT NULL COMMENT 'reife Kette, 1-basiert; NULL = ganze Sequenz bewerten',
  `reif_bis` int(11) DEFAULT NULL,
  `bemerkung` text DEFAULT NULL,
  `angelegt` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`kandidat_id`),
  UNIQUE KEY `uq_kurz` (`kurz`),
  UNIQUE KEY `uq_acc_basis` (`acc_basis`),
  KEY `k_rolle` (`rolle`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='Kandidaten: Stammdaten, je Protein eine Zeile';

-- Table: amyl_lauf
CREATE TABLE `amyl_lauf` (
  `lauf_id` int(11) NOT NULL AUTO_INCREMENT,
  `gestartet` datetime NOT NULL,
  `sekunden` float DEFAULT NULL,
  `host` varchar(32) DEFAULT NULL,
  `geraet` varchar(48) DEFAULT NULL,
  `software` varchar(200) DEFAULT NULL,
  `window_size` int(11) NOT NULL,
  `n_seq` int(11) DEFAULT NULL,
  `bemerkung` text DEFAULT NULL,
  PRIMARY KEY (`lauf_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='AmyloDeep-Laeufe (amylo_gpu.py)';

-- Table: amyl_sequenz
CREATE TABLE `amyl_sequenz` (
  `seq_id` int(11) NOT NULL AUTO_INCREMENT,
  `kandidat_id` int(11) NOT NULL,
  `acc` varchar(30) DEFAULT NULL COMMENT 'versioniert wie blast_acc.acc (P02766.1)',
  `quelle` varchar(40) NOT NULL COMMENT 'z.B. nr 2026-09-16, fasta, eingabe',
  `laenge` int(11) NOT NULL,
  `sequenz` mediumtext NOT NULL,
  `sha1` char(40) NOT NULL,
  `angelegt` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`seq_id`),
  UNIQUE KEY `uq_kand_sha1` (`kandidat_id`,`sha1`),
  KEY `k_acc` (`acc`),
  CONSTRAINT `fk_amyl_sequenz_kand` FOREIGN KEY (`kandidat_id`) REFERENCES `amyl_kandidat` (`kandidat_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='Sequenzversionen je Kandidat (eine Zeile je unterschiedlicher Sequenz)';

-- Table: blast_acc
CREATE TABLE `blast_acc` (
  `oid` int(10) unsigned NOT NULL,
  `pos` smallint(5) unsigned NOT NULL,
  `acc` varchar(32) NOT NULL,
  `taxid` int(10) unsigned NOT NULL,
  `acc_u` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci GENERATED ALWAYS AS (`acc`) VIRTUAL,
  PRIMARY KEY (`oid`,`pos`),
  KEY `idx_acc` (`acc`),
  KEY `idx_taxid` (`taxid`),
  KEY `idx_acc_u` (`acc_u`)
) ENGINE=InnoDB DEFAULT CHARSET=ascii COLLATE=ascii_general_ci ROW_FORMAT=COMPRESSED KEY_BLOCK_SIZE=8 COMMENT='NCBI nr: eine Zeile je Defline (zusammengefasste Eintraege haben mehrere Accessions je OID)';

-- Table: blast_api_hits
CREATE TABLE `blast_api_hits` (
  `ip` varbinary(16) NOT NULL COMMENT 'IPv4 bzw. IPv6-/64-Praefix',
  `minute` int(10) unsigned NOT NULL,
  `n` smallint(5) unsigned NOT NULL,
  PRIMARY KEY (`ip`,`minute`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='Ratenbegrenzung je Client und Minute';

-- Table: blast_api_key
CREATE TABLE `blast_api_key` (
  `key_id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `key_hash` char(64) NOT NULL COMMENT 'sha256 des Schluessels, Klartext wird nie gespeichert',
  `name` varchar(100) NOT NULL,
  `email` varchar(200) DEFAULT NULL,
  `jobs_pro_tag` smallint(5) unsigned NOT NULL DEFAULT 20,
  `aktiv` tinyint(1) NOT NULL DEFAULT 1,
  `erstellt` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`key_id`),
  UNIQUE KEY `uq_key_hash` (`key_hash`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci COMMENT='API-Schluessel fuer DIAMOND-Suchen';

-- Table: blast_dmnd_luecke
CREATE TABLE `blast_dmnd_luecke` (
  `oid` int(10) unsigned NOT NULL,
  PRIMARY KEY (`oid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='Leersequenzen der nr, nicht in nr_full.dmnd: dmnd-Satznummer = oid - (Zahl der Luecken mit kleinerer oid)';

-- Table: blast_import
CREATE TABLE `blast_import` (
  `chunk_no` int(10) unsigned NOT NULL,
  `release_id` int(10) unsigned NOT NULL DEFAULT 1,
  `first_oid` int(10) unsigned NOT NULL,
  `last_oid` int(10) unsigned NOT NULL,
  `n_seq` int(10) unsigned NOT NULL,
  `n_acc` int(10) unsigned NOT NULL,
  `sekunden` decimal(10,2) NOT NULL,
  `geladen` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`chunk_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='Fortschritt/Resume des nr-Metadatenimports';

-- Table: blast_job
CREATE TABLE `blast_job` (
  `job_id` char(24) NOT NULL COMMENT '96 bit Zufall, zugleich Lesezugriff auf das Ergebnis',
  `key_id` int(10) unsigned NOT NULL,
  `status` enum('queued','running','done','failed') NOT NULL DEFAULT 'queued',
  `mode` enum('blastp','blastx') NOT NULL,
  `sensitivity` varchar(16) NOT NULL,
  `evalue` double NOT NULL,
  `max_target_seqs` smallint(5) unsigned NOT NULL,
  `taxonlist` varchar(255) DEFAULT NULL,
  `len_min` int(10) unsigned DEFAULT NULL,
  `len_max` int(10) unsigned DEFAULT NULL,
  `new_days` smallint(5) unsigned DEFAULT NULL COMMENT 'NCBI createdate within the last N days',
  `query` mediumtext NOT NULL,
  `n_query` int(10) unsigned NOT NULL,
  `query_letters` int(10) unsigned NOT NULL,
  `erstellt` datetime NOT NULL DEFAULT current_timestamp(),
  `gestartet` datetime DEFAULT NULL,
  `fertig` datetime DEFAULT NULL,
  `sekunden` decimal(10,1) DEFAULT NULL,
  `release_id` int(10) unsigned DEFAULT NULL COMMENT 'nr-Stand, gegen den gesucht wurde',
  `dmnd_hash` char(32) DEFAULT NULL,
  `n_hits` int(10) unsigned DEFAULT NULL,
  `preselect` varchar(255) DEFAULT NULL COMMENT 'sub-.dmnd from dmnd_preselect.py or full-search fallback',
  `block_size` double DEFAULT NULL COMMENT 'chosen --block-size',
  `ram_est_gb` decimal(6,2) DEFAULT NULL,
  `ram_gb` decimal(6,2) DEFAULT NULL COMMENT 'measured peak RSS',
  `ram_free_gb` decimal(6,2) DEFAULT NULL COMMENT 'MemAvailable before the start',
  `error` text DEFAULT NULL,
  PRIMARY KEY (`job_id`),
  KEY `idx_status` (`status`,`erstellt`),
  KEY `idx_key` (`key_id`,`erstellt`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci COMMENT='DIAMOND-Suchauftraege der API';

-- Table: blast_job_hit
CREATE TABLE `blast_job_hit` (
  `job_id` char(24) NOT NULL,
  `n` int(10) unsigned NOT NULL,
  `qseqid` varchar(64) NOT NULL,
  `sseqid` varchar(32) NOT NULL,
  `pident` decimal(6,2) NOT NULL,
  `length` int(10) unsigned NOT NULL,
  `mismatch` int(10) unsigned NOT NULL,
  `gapopen` int(10) unsigned NOT NULL,
  `qstart` int(11) NOT NULL,
  `qend` int(11) NOT NULL,
  `sstart` int(10) unsigned NOT NULL,
  `send` int(10) unsigned NOT NULL,
  `evalue` double NOT NULL,
  `bitscore` decimal(9,1) NOT NULL,
  `staxids` varchar(255) NOT NULL,
  PRIMARY KEY (`job_id`,`n`)
) ENGINE=InnoDB DEFAULT CHARSET=ascii COLLATE=ascii_general_ci COMMENT='DIAMOND-Treffer je Auftrag (outfmt 6)';

-- Table: blast_ram_log
CREATE TABLE `blast_ram_log` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `job_id` char(24) DEFAULT NULL COMMENT 'NULL = manual measurement (block test etc.)',
  `mode` enum('blastp','blastx') NOT NULL,
  `sensitivity` varchar(16) NOT NULL,
  `query_letters` int(10) unsigned NOT NULL,
  `db_letters` bigint(20) unsigned NOT NULL COMMENT 'letters of the searched .dmnd (full or pre-selected sub-.dmnd)',
  `block_size` double NOT NULL COMMENT '--block-size in billions of letters',
  `eff_gletters` double NOT NULL COMMENT 'min(db_letters/1e9, block_size): billions of letters in the largest block',
  `threads` smallint(5) unsigned NOT NULL,
  `ram_gb` decimal(6,2) NOT NULL COMMENT 'peak RSS of diamond (/usr/bin/time %M)',
  `est_gb` decimal(6,2) DEFAULT NULL COMMENT 'estimate before the start',
  `free_gb` decimal(6,2) DEFAULT NULL COMMENT 'MemAvailable before the start',
  `seconds` decimal(10,1) DEFAULT NULL,
  `ok` tinyint(1) NOT NULL DEFAULT 1,
  `created` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_sens` (`mode`,`sensitivity`,`query_letters`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci COMMENT='Peak RSS of the DIAMOND searches, basis of the memory estimate';

-- Table: blast_ram_status
CREATE TABLE `blast_ram_status` (
  `id` tinyint(3) unsigned NOT NULL,
  `free_gb` decimal(6,2) NOT NULL COMMENT 'MemAvailable',
  `total_gb` decimal(6,2) NOT NULL,
  `swap_free_gb` decimal(6,2) NOT NULL,
  `updated` datetime NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='Current memory state for ?r=release (Apache cannot read /proc/meminfo), written by the worker every 30 s';

-- Table: blast_release
CREATE TABLE `blast_release` (
  `release_id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `quelle` varchar(16) NOT NULL DEFAULT 'nr' COMMENT 'nr = BLAST-nr-Snapshot, genbank-daily / refseq-daily = Tagesdatei',
  `datei` varchar(128) DEFAULT NULL COMMENT 'Tagesdatei, z.B. nc0918.gnp.gz',
  `nr_datum` datetime NOT NULL COMMENT 'Date aus blastdbcmd -db nr -info (NCBI-Stand)',
  `nr_sequenzen` bigint(20) unsigned NOT NULL,
  `nr_residues` bigint(20) unsigned NOT NULL,
  `taxdump_geladen` datetime DEFAULT NULL COMMENT 'Download von taxdump.tar.gz',
  `acc2taxid_geladen` datetime DEFAULT NULL COMMENT 'Download von prot.accession2taxid.FULL.gz',
  `dmnd_datei` varchar(255) DEFAULT NULL,
  `dmnd_hash` char(32) DEFAULT NULL COMMENT 'Database hash aus diamond dbinfo/makedb',
  `dmnd_sequenzen` bigint(20) unsigned DEFAULT NULL,
  `meta_importiert` datetime DEFAULT NULL COMMENT 'Ende blast_meta2db.py',
  `aktiv` tinyint(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (`release_id`),
  UNIQUE KEY `uq_quelle_datum` (`quelle`,`nr_datum`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='Welcher NCBI-nr-Stand liegt in blast_* und nr_full.dmnd';

-- Table: blast_seq
CREATE TABLE `blast_seq` (
  `oid` int(10) unsigned NOT NULL,
  `len` int(10) unsigned NOT NULL,
  `n_acc` smallint(5) unsigned NOT NULL,
  `title` text NOT NULL,
  PRIMARY KEY (`oid`),
  KEY `idx_len` (`len`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci ROW_FORMAT=COMPRESSED KEY_BLOCK_SIZE=8 COMMENT='NCBI nr: eine Zeile je Sequenz (OID wie in der BLAST-DB), Titel der ersten Defline';

-- Table: blast_seq_ncbi
CREATE TABLE `blast_seq_ncbi` (
  `oid` int(10) unsigned NOT NULL,
  `ncbi_datum` date NOT NULL COMMENT 'Datum der LOCUS-Zeile (letzte Aenderung bei NCBI)',
  `createdate` date DEFAULT NULL COMMENT 'NCBI esummary createdate, frueheste Accession der Sequenz',
  PRIMARY KEY (`oid`),
  KEY `idx_ncbi_datum` (`ncbi_datum`),
  KEY `idx_createdate` (`createdate`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='NCBI-Datum der per Tagesdelta angehaengten Sequenzen; nr-Snapshot hat keins';

-- Table: blast_taxon
CREATE TABLE `blast_taxon` (
  `taxid` int(10) unsigned NOT NULL,
  `parent` int(10) unsigned NOT NULL,
  `rank` varchar(32) NOT NULL,
  `name` varchar(255) NOT NULL,
  PRIMARY KEY (`taxid`),
  KEY `idx_parent` (`parent`),
  KEY `idx_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci COMMENT='NCBI Taxonomie (nodes.dmp + scientific name aus names.dmp)';

-- View: amyl_v_ergebnis
CREATE ALGORITHM=UNDEFINED SQL SECURITY DEFINER VIEW `amyl_v_ergebnis` AS select `e`.`lauf_id` AS `lauf_id`,`k`.`kandidat_id` AS `kandidat_id`,`k`.`kurz` AS `kurz`,`k`.`name` AS `name`,`k`.`rolle` AS `rolle`,`s`.`seq_id` AS `seq_id`,`s`.`acc` AS `acc`,`s`.`laenge` AS `laenge`,`e`.`bewertet_von` AS `bewertet_von`,`e`.`bewertet_bis` AS `bewertet_bis`,`e`.`bewertet_bis` - `e`.`bewertet_von` + 2 - `l`.`window_size` AS `n_fenster`,`e`.`avg_prob` AS `avg_prob`,`e`.`max_prob` AS `max_prob`,`e`.`max_pos` AS `max_pos`,substr(`s`.`sequenz`,`e`.`max_pos`,`l`.`window_size`) AS `max_fenster`,`e`.`sekunden` AS `sekunden`,`b`.`title` AS `titel`,`a`.`taxid` AS `taxid`,`t`.`name` AS `taxon` from ((((((`amyl_ergebnis` `e` join `amyl_lauf` `l` on(`l`.`lauf_id` = `e`.`lauf_id`)) join `amyl_sequenz` `s` on(`s`.`seq_id` = `e`.`seq_id`)) join `amyl_kandidat` `k` on(`k`.`kandidat_id` = `s`.`kandidat_id`)) left join `blast_acc` `a` on(`a`.`acc` = `s`.`acc`)) left join `blast_seq` `b` on(`b`.`oid` = `a`.`oid`)) left join `blast_taxon` `t` on(`t`.`taxid` = `a`.`taxid`));

-- View: amyl_v_fenster
CREATE ALGORITHM=UNDEFINED SQL SECURITY DEFINER VIEW `amyl_v_fenster` AS select `f`.`lauf_id` AS `lauf_id`,`k`.`kurz` AS `kurz`,`s`.`acc` AS `acc`,`f`.`seq_id` AS `seq_id`,`f`.`pos` AS `pos`,substr(`s`.`sequenz`,`f`.`pos`,`l`.`window_size`) AS `fenster`,`f`.`prob` AS `prob` from (((`amyl_fenster` `f` join `amyl_lauf` `l` on(`l`.`lauf_id` = `f`.`lauf_id`)) join `amyl_sequenz` `s` on(`s`.`seq_id` = `f`.`seq_id`)) join `amyl_kandidat` `k` on(`k`.`kandidat_id` = `s`.`kandidat_id`));

-- View: blast_acc_herkunft
CREATE ALGORITHM=UNDEFINED SQL SECURITY DEFINER VIEW `blast_acc_herkunft` AS select `a`.`acc` AS `acc`,`a`.`oid` AS `oid`,`a`.`taxid` AS `taxid`,`r`.`quelle` AS `quelle`,`r`.`datei` AS `datei`,`r`.`nr_datum` AS `nr_stand`,`n`.`ncbi_datum` AS `ncbi_datum`,`i`.`geladen` AS `hinzugefuegt` from (((`blast_acc` `a` join `blast_import` `i` on(`a`.`oid` between `i`.`first_oid` and `i`.`last_oid`)) join `blast_release` `r` on(`r`.`release_id` = `i`.`release_id`)) left join `blast_seq_ncbi` `n` on(`n`.`oid` = `a`.`oid`));

-- View: blast_seq_herkunft
CREATE ALGORITHM=UNDEFINED SQL SECURITY DEFINER VIEW `blast_seq_herkunft` AS select `s`.`oid` AS `oid`,`s`.`len` AS `len`,`s`.`n_acc` AS `n_acc`,`s`.`title` AS `title`,`r`.`release_id` AS `release_id`,`r`.`quelle` AS `quelle`,`r`.`datei` AS `datei`,`r`.`nr_datum` AS `nr_stand`,`n`.`ncbi_datum` AS `ncbi_datum`,`i`.`geladen` AS `hinzugefuegt` from (((`blast_seq` `s` join `blast_import` `i` on(`s`.`oid` between `i`.`first_oid` and `i`.`last_oid`)) join `blast_release` `r` on(`r`.`release_id` = `i`.`release_id`)) left join `blast_seq_ncbi` `n` on(`n`.`oid` = `s`.`oid`));

