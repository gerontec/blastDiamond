#!/usr/bin/env python3
"""AmyloDeep (PyPI amylodeep 0.3.1) auf der Tesla P4.

Das Paket rechnet alles auf der CPU und fuehrt ESM-2 650M je Fenster dreimal aus
(NN-Kopf, SVM, XGBoost bekommen dieselben Mean-Embeddings). Hier: Torch-Modelle auf
CUDA, 650M-Embeddings je Fenstermenge einmal. UniRep (JAX), SVM, XGBoost bleiben CPU.
Ergebnis identisch zum Original bis auf Float-Rundung (--vergleich prueft das).

    amylo_gpu.py --kandidaten [ROLLE]          Kandidaten aus amyl_kandidat (Standard: amyloid)
    amylo_gpu.py --import-tsv datei.tsv [--rolle kontrolle]   Kandidaten anlegen/aktualisieren (acc, kurz, name)
    amylo_gpu.py SEQUENZ | -i datei.fa | --acc P02766 [..]   Einzelabfragen (rolle einzel)
                 [-w 10] [-o out.tsv] [--cpu] [--vergleich] [--keine-db] [--bemerkung TEXT]
Sequenzen zu Accessions per blastdbcmd aus der lokalen nr (~/blastdb). Schema: amyl_schema.sql
(amyl_kandidat -> amyl_sequenz -> amyl_ergebnis -> amyl_fenster; Fenstertexte nur in den Views).
Ist reif_von/reif_bis gesetzt, wird nur die reife Kette bewertet, Positionen bleiben auf die ganze Sequenz bezogen.
Env: ~/iver_sim/mamba/envs/iver (CUDA 11.8, PyTorch 2.5.1)."""
import argparse
import hashlib
import os
import socket
import subprocess
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F

import amylodeep
from amylodeep import EnsembleRollingWindowPredictor, load_models_and_calibrators

sys.path.insert(0, os.path.expanduser("~/iver_sim"))
import iv_db  # noqa: E402  (Zugangsdaten wagodb)

BATCH = 64
BLASTDBCMD = os.path.expanduser("~/iver_sim/mamba/envs/blast/bin/blastdbcmd")
BLASTDB = os.path.expanduser("~/blastdb/nr")

SCHEMA_DATEI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "amyl_schema.sql")


class GPUPredictor(EnsembleRollingWindowPredictor):
    def __init__(self, models, calibrators, tokenizer, device):
        super().__init__(models, calibrators, tokenizer)
        self.dev = torch.device(device)
        for k in ("esm2_150M", "esm2_650M", "unirep"):
            self.models[k].to(self.dev).eval()
        self.esm_model.to(self.dev)
        self._emb_key, self._emb = None, None

    def _esm650_mean(self, sequences):
        """Mean-gepoolte ESM-2-650M-Embeddings (wie das Original, max_length 128), einmal je Fenstermenge."""
        key = tuple(sequences)
        if key != self._emb_key:
            enc = self.tokenizer_esm(sequences, padding="max_length", truncation=True, max_length=128,
                                     return_tensors="pt")
            out = []
            with torch.no_grad():
                for i in range(0, len(sequences), BATCH):
                    ids = enc["input_ids"][i:i + BATCH].to(self.dev)
                    mask = enc["attention_mask"][i:i + BATCH].to(self.dev)
                    h = self.esm_model(input_ids=ids, attention_mask=mask).last_hidden_state
                    m = mask.unsqueeze(-1).float()
                    out.append((h * m).sum(1) / m.sum(1).clamp(min=1e-9))
            self._emb_key, self._emb = key, torch.cat(out)
        return self._emb

    def _predict_model_1(self, sequences):
        enc = self.tokenizer_1(sequences, padding="max_length", truncation=True, max_length=128)
        ids, mask = torch.tensor(enc["input_ids"]), torch.tensor(enc["attention_mask"])
        probs = []
        with torch.no_grad():
            for i in range(0, len(sequences), BATCH):
                logits = self.models["esm2_150M"](input_ids=ids[i:i + BATCH].to(self.dev),
                                                  attention_mask=mask[i:i + BATCH].to(self.dev)).logits
                probs.append(F.softmax(logits, dim=1)[:, 1].cpu())
        return torch.cat(probs).numpy()

    def _predict_model_2(self, sequences):
        import jax_unirep
        h_final, _, _ = jax_unirep.get_reps(sequences)
        with torch.no_grad():
            logits = self.models["unirep"](embeddings=torch.tensor(np.asarray(h_final), dtype=torch.float32,
                                                                   device=self.dev))["logits"]
        p = F.softmax(logits, dim=1)[:, 1].cpu().numpy()
        if "platt_unirep" in self.calibrators:
            p = self.calibrators["platt_unirep"].predict_proba(p.reshape(-1, 1))[:, 1]
        return p

    def _predict_model_3(self, sequences):
        with torch.no_grad():
            logits = self.models["esm2_650M"](embeddings=self._esm650_mean(sequences))["logits"]
        p = F.softmax(logits, dim=1)[:, 1].cpu().numpy()
        if "isotonic_650M_NN" in self.calibrators:
            p = self.calibrators["isotonic_650M_NN"].predict(p)
        return p

    def _extract_features_for_svm(self, sequences):
        return self._esm650_mean(sequences).cpu().numpy()


def fasta(path):
    recs, name, seq = [], None, []
    for line in open(path):
        line = line.strip()
        if line.startswith(">"):
            if name is not None:
                recs.append((name, "".join(seq).upper()))
            name, seq = line[1:].split()[0] if line[1:].strip() else f"seq{len(recs) + 1}", []
        elif line:
            seq.append(line)
    if name is not None:
        recs.append((name, "".join(seq).upper()))
    return [{"seq_id": n, "seq": s} for n, s in recs]


def aus_nr(acc):
    """(versionierte Accession, Sequenz) aus der lokalen nr, oder None."""
    r = subprocess.run([BLASTDBCMD, "-db", BLASTDB, "-entry", acc, "-target_only", "-outfmt", "%a\t%s"],
                       capture_output=True, text=True)
    zeile = r.stdout.strip().split("\n")[0]
    if "\t" not in zeile:
        print(f"{acc}: nicht in nr, uebersprungen ({r.stderr.strip()[:120]})", file=sys.stderr)
        return None
    vacc, seq = zeile.split("\t")
    return vacc, seq.upper()


def sql_datei(c, pfad):
    for s in open(pfad).read().split(";\n"):
        if s.strip() and not all(z.startswith("--") or not z.strip() for z in s.splitlines()):
            c.execute(s)


def kandidat_id(db, acc_basis=None, name=None):
    """Einzelabfrage: vorhandenen Kandidaten finden oder als rolle 'einzel' anlegen."""
    if acc_basis:
        db.execute("SELECT kandidat_id FROM amyl_kandidat WHERE acc_basis = %s", (acc_basis,))
    else:
        db.execute("SELECT kandidat_id FROM amyl_kandidat WHERE acc_basis IS NULL AND name = %s", (name,))
    row = db.fetchone()
    if row:
        return row[0]
    db.execute("INSERT INTO amyl_kandidat (acc_basis, name, rolle) VALUES (%s, %s, 'einzel')", (acc_basis, name))
    return db.lastrowid


def sequenz_id(db, kid, acc, quelle, seq):
    """Sequenzversion je Kandidat genau einmal (SHA1)."""
    sha = hashlib.sha1(seq.encode()).hexdigest()
    db.execute("SELECT seq_id FROM amyl_sequenz WHERE kandidat_id = %s AND sha1 = %s", (kid, sha))
    row = db.fetchone()
    if row:
        return row[0]
    db.execute("INSERT INTO amyl_sequenz (kandidat_id, acc, quelle, laenge, sequenz, sha1) "
               "VALUES (%s, %s, %s, %s, %s, %s)", (kid, acc, quelle, len(seq), seq, sha))
    return db.lastrowid


def import_tsv(db, pfad, rolle):
    """acc, kurz, name je Zeile; vorhandene Kandidaten (gleiche acc_basis) werden aktualisiert."""
    n = 0
    for zeile in open(pfad):
        if not zeile.strip():
            continue
        acc, kurz, name = (zeile.rstrip("\n").split("\t") + ["", ""])[:3]
        db.execute("INSERT INTO amyl_kandidat (acc_basis, kurz, name, rolle) VALUES (%s, %s, %s, %s) "
                   "ON DUPLICATE KEY UPDATE kurz = VALUES(kurz), name = VALUES(name), rolle = VALUES(rolle)",
                   (acc.split(".")[0], kurz or None, name or None, rolle))
        n += 1
    print(f"{n} Kandidaten ({rolle}) angelegt/aktualisiert", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sequence", nargs="?")
    ap.add_argument("-i", "--input")
    ap.add_argument("--acc", nargs="+", help="Accessions, Sequenz aus der lokalen nr")
    ap.add_argument("--kandidaten", nargs="?", const="amyloid", metavar="ROLLE",
                    help="alle Kandidaten dieser Rolle aus amyl_kandidat (amyloid, kontrolle, einzel)")
    ap.add_argument("--import-tsv", metavar="DATEI", help="Kandidaten anlegen/aktualisieren und beenden")
    ap.add_argument("--rolle", default="amyloid", choices=["amyloid", "kontrolle", "einzel"])
    ap.add_argument("-w", "--window-size", type=int, default=10)
    ap.add_argument("-o", "--output")
    ap.add_argument("--cpu", action="store_true", help="ohne GPU rechnen")
    ap.add_argument("--vergleich", action="store_true", help="zusaetzlich das unveraenderte Original (CPU) rechnen")
    ap.add_argument("--keine-db", action="store_true", help="nicht nach wagodb schreiben (nur Einzelabfragen)")
    ap.add_argument("--bemerkung")
    a = ap.parse_args()
    if a.keine_db and (a.kandidaten or a.import_tsv):
        ap.error("--kandidaten und --import-tsv brauchen die Datenbank")

    db = None
    if not a.keine_db:
        db = iv_db.conn().cursor()
        sql_datei(db, SCHEMA_DATEI)
    if a.import_tsv:
        import_tsv(db, a.import_tsv, a.rolle)
        return

    # recs: kid (oder None ohne DB), anzeige, acc, quelle, sequenz, reif_von, reif_bis
    recs = []
    if a.kandidaten:
        db.execute("SELECT kandidat_id, COALESCE(kurz, name, acc_basis), acc_basis, reif_von, reif_bis "
                   "FROM amyl_kandidat WHERE rolle = %s AND acc_basis IS NOT NULL ORDER BY kandidat_id",
                   (a.kandidaten,))
        for kid, anzeige, acc_basis, rv, rb in db.fetchall():
            nr = aus_nr(acc_basis)
            if nr:
                recs.append((kid, anzeige, nr[0], "nr", nr[1], rv, rb))
    elif a.acc:
        for x in a.acc:
            nr = aus_nr(x)
            if nr:
                kid = kandidat_id(db, acc_basis=x.split(".")[0]) if db else None
                recs.append((kid, nr[0], nr[0], "nr", nr[1], None, None))
    elif a.input:
        for r in fasta(a.input):
            kid = kandidat_id(db, name=r["seq_id"]) if db else None
            recs.append((kid, r["seq_id"], None, "fasta", r["seq"], None, None))
    elif a.sequence:
        kid = kandidat_id(db, name="eingabe") if db else None
        recs.append((kid, "eingabe", None, "eingabe", a.sequence.upper(), None, None))
    else:
        ap.error("SEQUENZ, -i datei.fa, --acc, --kandidaten oder --import-tsv")

    dev = "cpu" if a.cpu or not torch.cuda.is_available() else "cuda"
    gpu = torch.cuda.get_device_name(0) if dev == "cuda" else "cpu"
    t_lauf = time.time()
    models, cal, tok = load_models_and_calibrators()
    pred = GPUPredictor(models, cal, tok, dev)
    print(f"Modelle geladen in {time.time() - t_lauf:.1f}s, Geraet {gpu}", file=sys.stderr)

    if db:
        sw = (f"amylodeep {amylodeep.__version__}, torch {torch.__version__}, cuda {torch.version.cuda}, "
              f"amylo_gpu.py batch {BATCH}")
        db.execute("INSERT INTO amyl_lauf (gestartet, host, geraet, software, window_size, n_seq, bemerkung) "
                   "VALUES (NOW(), %s, %s, %s, %s, %s, %s)",
                   (socket.gethostname(), gpu, sw, a.window_size, len(recs), a.bemerkung))
        lauf = db.lastrowid
        print(f"amyl_lauf.lauf_id = {lauf}", file=sys.stderr)

    out = open(a.output, "w") if a.output else sys.stdout
    print("id\tpos\tfenster\tprob", file=out)
    for kid, anzeige, acc, quelle, seq, reif_von, reif_bis in recs:
        von = reif_von or 1
        bis = min(reif_bis or len(seq), len(seq))
        teil = seq[von - 1:bis]
        t0 = time.time()
        r = pred.rolling_window_prediction(teil, a.window_size)
        dt = time.time() - t0
        rows = [(von + pos, float(p)) for pos, p in r["position_probs"]]   # Position in der ganzen Sequenz
        for pos, p in rows:
            print(f"{anzeige}\t{pos}\t{seq[pos - 1:pos - 1 + a.window_size]}\t{p:.4f}", file=out)
        best = max(rows, key=lambda x: x[1])
        print(f"{anzeige}: {len(seq)} aa" + (f" (bewertet {von}-{bis})" if (von, bis) != (1, len(seq)) else "")
              + f", {len(rows)} Fenster, {dt:.1f}s, mittel {r['avg_probability']:.4f}, max {best[1]:.4f} bei "
              f"{best[0]} {seq[best[0] - 1:best[0] - 1 + a.window_size]}", file=sys.stderr)
        if db:
            db.connection.begin()
            sid = sequenz_id(db, kid, acc, quelle if quelle != "nr" else "nr 2026-09-16", seq)
            db.execute("INSERT INTO amyl_ergebnis (lauf_id, seq_id, bewertet_von, bewertet_bis, avg_prob, "
                       "max_prob, max_pos, sekunden) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                       (lauf, sid, von, bis, float(r["avg_probability"]), best[1], best[0], round(dt, 2)))
            db.executemany("INSERT INTO amyl_fenster (lauf_id, seq_id, pos, prob) VALUES (%s,%s,%s,%s)",
                           [(lauf, sid, pos, p) for pos, p in rows])
            db.connection.commit()
        if a.vergleich:
            if dev == "cuda":
                torch.cuda.empty_cache()
            orig = EnsembleRollingWindowPredictor(models, cal, tok)
            for k in ("esm2_150M", "esm2_650M", "unirep"):
                models[k].to("cpu")
            t0 = time.time()
            ro = orig.rolling_window_prediction(teil, a.window_size)
            diff = np.max(np.abs(np.array([p for _, p in r["position_probs"]])
                                 - np.array([p for _, p in ro["position_probs"]])))
            print(f"{anzeige}: Original (CPU) {time.time() - t0:.1f}s, max. Abweichung {diff:.2e}", file=sys.stderr)
            for k in ("esm2_150M", "esm2_650M", "unirep"):
                models[k].to(pred.dev)
    if db:
        db.execute("UPDATE amyl_lauf SET sekunden = %s WHERE lauf_id = %s", (round(time.time() - t_lauf, 1), lauf))


if __name__ == "__main__":
    main()
