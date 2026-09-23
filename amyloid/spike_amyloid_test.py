#!/usr/bin/env python3
"""Prueft eine veroeffentlichte Aussage gegen AmyloDeep: Ist Spike 194-203 (FKNIDGYFKI) auffaellig?

Hintergrund (Nutzerangabe, in dieser Sitzung nicht nachgeprueft): Nystroem & Hammarstroem, JACS 2022,
berichten sieben amyloidogene Abschnitte im SARS-CoV-2-Spike; das Fragment 194-203 bilde nach Spaltung
durch neutrophile Elastase Fibrillen. Geprueft wird hier nur die Vorhersage von AmyloDeep, nicht die
Arbeit selbst; Spaltstellen sagt AmyloDeep ohnehin nicht vorher.

Ein hoher Wert allein sagt nichts (im 43-Kandidaten-Lauf lagen alle Maxima zwischen 0,88 und 0,97).
Deshalb zwei Bezugsgroessen:
  - intern: Rang des Fensters 194 unter allen 1.264 Fenstern des Spike-Proteins,
  - extern: Verteilung aller Fenster eines Kontrollsatzes zufaelliger humaner SwissProt-Proteine.

    spike_amyloid_test.py --vorbereiten [--n-kontrollen 300] [--seed 42]
    (dann rechnen:)  amylo_gpu.py --acc YP_009724390.1 ;  amylo_gpu.py --kandidaten kontrolle
    spike_amyloid_test.py --auswerten

Sequenzen kommen per Accession aus der lokalen nr, Ergebnisse stehen in wagodb (amyl_*)."""
import argparse
import os
import random
import sys

sys.path.insert(0, os.path.expanduser("~/iver_sim"))
import iv_db  # noqa: E402

HIER = os.path.dirname(os.path.abspath(__file__))
SWISSPROT = os.path.join(HIER, "swissprot_titel.tsv")
SPIKE = "YP_009724390"
FRAGMENT = (194, 203, "FKNIDGYFKI")
# Kontrollen sollen unverdaechtig sein: bekannte Amyloidbildner und Verwandtes ausschliessen.
AUS = ("amyloid", "prion", "synuclein", "huntingt", "ataxin", "transthyretin", "cystatin", "gelsolin",
       "apolipoprotein", "lysozyme", "insulin", "calcitonin", "prolactin", "semenogelin", "fibrinogen",
       "beta-2-micro", "immunoglobulin", "tau ", "tdp-43", "fus ", "superoxide dismutase", "lactotransferrin",
       "galectin", "corneodesmosin", "lactadherin", "natriuretic", "surfactant", "chemotaxin", "serum amyloid")


def vorbereiten(db, n, seed):
    db.execute("INSERT INTO amyl_kandidat (acc_basis, kurz, name, rolle, bemerkung) "
               "VALUES (%s, 'Spike', 'SARS-CoV-2 surface glycoprotein', 'einzel', %s) "
               "ON DUPLICATE KEY UPDATE kurz = VALUES(kurz), name = VALUES(name), bemerkung = VALUES(bemerkung)",
               (SPIKE, "Volllaenge 1273 aa; Testfall fuer Fragment 194-203"))
    print(f"Kandidat Spike ({SPIKE}) angelegt, ganze Sequenz (die 43 Amyloid-Kandidaten bleiben unberuehrt)")

    zeilen = []
    with open(SWISSPROT, encoding="utf-8", errors="replace") as f:
        for z in f:
            t = z.rstrip("\n").split("\t")
            if len(t) < 4 or t[1] != "9606":
                continue
            if not (100 <= int(t[2]) <= 800):
                continue
            if any(w in t[3].lower() for w in AUS):
                continue
            zeilen.append((t[0], t[3]))
    print(f"{len(zeilen)} humane SwissProt-Proteine (100-800 aa, ohne bekannte Amyloidbildner) zur Auswahl")

    random.seed(seed)
    ausgewaehlt = random.sample(zeilen, min(n, len(zeilen)))
    neu = 0
    for acc, titel in ausgewaehlt:
        name = titel.split("Full=")[1].split(";")[0][:160] if "Full=" in titel else titel[:160]
        db.execute("INSERT IGNORE INTO amyl_kandidat (acc_basis, name, rolle, bemerkung) "
                   "VALUES (%s, %s, 'kontrolle', %s)",
                   (acc.split(".")[0], name, f"SwissProt-Zufallsauswahl, seed {seed}"))
        neu += db.rowcount
    print(f"{neu} Kontrollen angelegt (seed {seed}), rolle=kontrolle")
    print("\nJetzt rechnen (dauert bei 300 Kontrollen mehrere Stunden auf der P4):")
    print("  PY=~/iver_sim/mamba/envs/iver/bin/python")
    print("  $PY amylo_gpu.py --acc YP_009724390.1   --bemerkung 'Spike-Test: Volllaenge'")
    print("  $PY amylo_gpu.py --kandidaten kontrolle --bemerkung 'Spike-Test: SwissProt-Kontrollen'")
    print("  python3 spike_amyloid_test.py --auswerten")


def auswerten(db, fenster_zeigen):
    von, bis, seq_soll = FRAGMENT
    db.execute("SELECT e.lauf_id, s.seq_id, l.window_size, s.laenge, SUBSTRING(s.sequenz, %s, %s) "
               "FROM amyl_ergebnis e JOIN amyl_lauf l ON l.lauf_id = e.lauf_id "
               "JOIN amyl_sequenz s ON s.seq_id = e.seq_id JOIN amyl_kandidat k ON k.kandidat_id = s.kandidat_id "
               "WHERE k.acc_basis = %s ORDER BY e.lauf_id DESC LIMIT 1", (von, bis - von + 1, SPIKE))
    row = db.fetchone()
    if not row:
        sys.exit("Kein Spike-Ergebnis in amyl_ergebnis - erst amylo_gpu.py --acc YP_009724390.1 laufen lassen")
    lauf, seq_id, w, laenge, seq_ist = row
    print(f"Spike: Lauf {lauf}, {laenge} aa, Fenster {w}")
    if seq_ist != seq_soll:
        sys.exit(f"FEHLER: Reste {von}-{bis} sind {seq_ist!r}, erwartet {seq_soll!r} - andere Sequenzversion?")
    print(f"Reste {von}-{bis} = {seq_ist} wie erwartet")

    db.execute("SELECT pos, prob, RANK() OVER (ORDER BY prob DESC) FROM amyl_fenster "
               "WHERE lauf_id = %s AND seq_id = %s ORDER BY pos", (lauf, seq_id))
    fenster = db.fetchall()
    treffer = {p: (pr, rg) for p, pr, rg in fenster if p == von}
    if von not in treffer:
        sys.exit(f"Kein Fenster an Position {von}")
    prob, rang = treffer[von]
    print(f"\n--- intern (im Spike selbst) ---")
    print(f"Fenster {von} ({seq_ist}): prob {prob:.4f}, Rang {rang} von {len(fenster)} "
          f"(besser als {100 * (len(fenster) - rang) / len(fenster):.1f} % der Spike-Fenster)")
    print(f"staerkste Fenster des Spike:")
    for p, pr, rg in sorted(fenster, key=lambda x: -x[1])[:fenster_zeigen]:
        db.execute("SELECT SUBSTRING(sequenz, %s, %s) FROM amyl_sequenz WHERE seq_id = %s", (p, w, seq_id))
        print(f"  Rang {rg:4d}  Pos {p:5d}  {db.fetchone()[0]}  {pr:.4f}"
              + ("   <-- 194-203" if p == von else ""))

    db.execute("SELECT COUNT(DISTINCT f.seq_id), COUNT(*), SUM(f.prob >= %s) FROM amyl_fenster f "
               "JOIN amyl_sequenz s ON s.seq_id = f.seq_id JOIN amyl_kandidat k ON k.kandidat_id = s.kandidat_id "
               "WHERE k.rolle = 'kontrolle'", (prob,))
    n_prot, n_fen, n_hoeher = db.fetchone()
    print(f"\n--- extern (SwissProt-Kontrollen) ---")
    if not n_fen:
        print("noch keine Kontrollen gerechnet (amylo_gpu.py --kandidaten kontrolle)")
        return
    anteil = 100 * int(n_hoeher) / n_fen
    print(f"{n_prot} Kontrollproteine, {n_fen:,} Fenster".replace(",", "."))
    print(f"{n_hoeher:,} davon erreichen {prob:.4f} oder mehr = {anteil:.2f} %".replace(",", "."))
    db.execute("SELECT MAX(je.mx), AVG(je.mx) FROM (SELECT MAX(f.prob) AS mx FROM amyl_fenster f "
               "JOIN amyl_sequenz s ON s.seq_id = f.seq_id JOIN amyl_kandidat k ON k.kandidat_id = s.kandidat_id "
               "WHERE k.rolle = 'kontrolle' GROUP BY f.seq_id) je")
    mx, mit = db.fetchone()
    print(f"Maximum je Kontrollprotein: hoechstes {mx:.4f}, im Mittel {mit:.4f}")
    print(f"\nLesart: je kleiner der Anteil, desto auffaelliger ist {seq_ist}. Liegt er im Bereich vieler "
          f"Kontrollfenster, trennt AmyloDeep diesen Abschnitt nicht vom Hintergrund.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vorbereiten", action="store_true")
    ap.add_argument("--auswerten", action="store_true")
    ap.add_argument("--n-kontrollen", type=int, default=300)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--top", type=int, default=10, help="wie viele Spike-Fenster zeigen")
    a = ap.parse_args()
    if not (a.vorbereiten or a.auswerten):
        ap.error("--vorbereiten oder --auswerten")
    db = iv_db.conn().cursor()
    if a.vorbereiten:
        vorbereiten(db, a.n_kontrollen, a.seed)
    if a.auswerten:
        auswerten(db, a.top)


if __name__ == "__main__":
    main()
