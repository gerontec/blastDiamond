-- AmyloDeep-Ergebnisse in wagodb, Praefix amyl_. Jede Angabe steht genau einmal:
-- Kandidat (Liste) -> Sequenzversion -> Ergebnis je Lauf -> Fenster. Fenstertexte, Fensterzahl und
-- NCBI-Titel/Taxon werden nicht gespeichert, sondern in den Views abgeleitet.
-- Gelesen von amylo_gpu.py und amyl_migration.py (Anweisungen durch ";" am Zeilenende getrennt).

CREATE TABLE IF NOT EXISTS amyl_lauf (
  lauf_id      INT AUTO_INCREMENT PRIMARY KEY,
  gestartet    DATETIME NOT NULL,
  sekunden     FLOAT,
  host         VARCHAR(32),
  geraet       VARCHAR(48),
  software     VARCHAR(200),
  window_size  INT NOT NULL,
  n_seq        INT,
  bemerkung    TEXT
) ENGINE=InnoDB COMMENT='AmyloDeep-Laeufe (amylo_gpu.py)';

CREATE TABLE IF NOT EXISTS amyl_kandidat (
  kandidat_id  INT AUTO_INCREMENT PRIMARY KEY,
  kurz         VARCHAR(16)  COMMENT 'Amyloid-Kuerzel (ATTR, Abeta, ...); NULL bei Einzelabfragen',
  name         VARCHAR(160) COMMENT 'Proteinname laut Kandidatenliste bzw. FASTA-Kennung',
  acc_basis    VARCHAR(24)  COMMENT 'Accession ohne Version (P02766); Sequenz kommt aus nr',
  rolle        ENUM('amyloid','kontrolle','einzel') NOT NULL DEFAULT 'amyloid',
  reif_von     INT COMMENT 'reife Kette, 1-basiert; NULL = ganze Sequenz bewerten',
  reif_bis     INT,
  bemerkung    TEXT,
  angelegt     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_kurz (kurz),
  UNIQUE KEY uq_acc_basis (acc_basis),
  KEY k_rolle (rolle)
) ENGINE=InnoDB COMMENT='Kandidaten: Stammdaten, je Protein eine Zeile';

CREATE TABLE IF NOT EXISTS amyl_sequenz (
  seq_id       INT AUTO_INCREMENT PRIMARY KEY,
  kandidat_id  INT NOT NULL,
  acc          VARCHAR(30) COMMENT 'versioniert wie blast_acc.acc (P02766.1)',
  quelle       VARCHAR(40) NOT NULL COMMENT 'z.B. nr 2026-09-16, fasta, eingabe',
  laenge       INT NOT NULL,
  sequenz      MEDIUMTEXT NOT NULL,
  sha1         CHAR(40) NOT NULL,
  angelegt     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_kand_sha1 (kandidat_id, sha1),
  KEY k_acc (acc),
  CONSTRAINT fk_amyl_sequenz_kand FOREIGN KEY (kandidat_id) REFERENCES amyl_kandidat(kandidat_id)
) ENGINE=InnoDB COMMENT='Sequenzversionen je Kandidat (eine Zeile je unterschiedlicher Sequenz)';

CREATE TABLE IF NOT EXISTS amyl_ergebnis (
  lauf_id      INT NOT NULL,
  seq_id       INT NOT NULL,
  bewertet_von INT NOT NULL COMMENT '1-basiert; >1 wenn nur die reife Kette bewertet wurde',
  bewertet_bis INT NOT NULL,
  avg_prob     FLOAT NOT NULL COMMENT 'Mittel ueber alle Fenster',
  max_prob     FLOAT NOT NULL,
  max_pos      INT NOT NULL COMMENT '1-basiert, Beginn des staerksten Fensters',
  sekunden     FLOAT,
  PRIMARY KEY (lauf_id, seq_id),
  CONSTRAINT fk_amyl_erg_lauf FOREIGN KEY (lauf_id) REFERENCES amyl_lauf(lauf_id),
  CONSTRAINT fk_amyl_erg_seq FOREIGN KEY (seq_id) REFERENCES amyl_sequenz(seq_id)
) ENGINE=InnoDB COMMENT='AmyloDeep je Lauf und Sequenz';

CREATE TABLE IF NOT EXISTS amyl_fenster (
  lauf_id      INT NOT NULL,
  seq_id       INT NOT NULL,
  pos          INT NOT NULL COMMENT '1-basiert, bezogen auf die ganze Sequenz',
  prob         FLOAT NOT NULL COMMENT 'Ensemble-Wahrscheinlichkeit amyloidogen',
  PRIMARY KEY (lauf_id, seq_id, pos),
  CONSTRAINT fk_amyl_fenster_erg FOREIGN KEY (lauf_id, seq_id) REFERENCES amyl_ergebnis(lauf_id, seq_id)
) ENGINE=InnoDB COMMENT='AmyloDeep je Fenster; Fenstertext = SUBSTRING(sequenz, pos, window_size)';

CREATE OR REPLACE VIEW amyl_v_ergebnis AS
SELECT e.lauf_id, k.kandidat_id, k.kurz, k.name, k.rolle, s.seq_id, s.acc, s.laenge,
       e.bewertet_von, e.bewertet_bis, e.bewertet_bis - e.bewertet_von + 2 - l.window_size AS n_fenster,
       e.avg_prob, e.max_prob, e.max_pos, SUBSTRING(s.sequenz, e.max_pos, l.window_size) AS max_fenster,
       e.sekunden, b.title AS titel, a.taxid, t.name AS taxon
FROM amyl_ergebnis e
JOIN amyl_lauf l ON l.lauf_id = e.lauf_id
JOIN amyl_sequenz s ON s.seq_id = e.seq_id
JOIN amyl_kandidat k ON k.kandidat_id = s.kandidat_id
LEFT JOIN blast_acc a ON a.acc = s.acc
LEFT JOIN blast_seq b ON b.oid = a.oid
LEFT JOIN blast_taxon t ON t.taxid = a.taxid;

CREATE OR REPLACE VIEW amyl_v_fenster AS
SELECT f.lauf_id, k.kurz, s.acc, f.seq_id, f.pos, SUBSTRING(s.sequenz, f.pos, l.window_size) AS fenster, f.prob
FROM amyl_fenster f
JOIN amyl_lauf l ON l.lauf_id = f.lauf_id
JOIN amyl_sequenz s ON s.seq_id = f.seq_id
JOIN amyl_kandidat k ON k.kandidat_id = s.kandidat_id;

CREATE TABLE IF NOT EXISTS amyl_job (
  job_id       CHAR(24) NOT NULL PRIMARY KEY COMMENT '96 bit Zufall, zugleich Leseberechtigung',
  key_id       INT NOT NULL COMMENT 'blast_api_key.key_id (gemeinsame Schluessel mit der DIAMOND-API)',
  status       ENUM('queued','running','done','failed') NOT NULL DEFAULT 'queued',
  acc          VARCHAR(30) COMMENT 'Accession aus nr; entweder acc oder query',
  bereich_von  INT, bereich_bis INT COMMENT 'Teilbereich der Sequenz, 1-basiert',
  query        MEDIUMTEXT COMMENT 'uebergebene Sequenz (nur wenn keine Accession)',
  window_size  INT NOT NULL DEFAULT 10,
  erstellt     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gestartet    DATETIME, fertig DATETIME, sekunden FLOAT,
  lauf_id      INT COMMENT 'amyl_lauf des Worker-Laufs',
  seq_id       INT COMMENT 'amyl_sequenz der bewerteten Sequenz',
  error        TEXT,
  KEY k_status (status, erstellt),
  KEY k_key (key_id)
) ENGINE=InnoDB COMMENT='Warteschlange der AmyloDeep-API (Worker amylo_api_worker.py)';
