#!/usr/bin/env python3
"""Erzeugt die Konzept-Doku BLAST -> DIAMOND als PDF, deutsch und englisch (LaTeX, pdflatex).

    python3 ~/python/blast_diamond_pdf.py [zielverzeichnis]     (Standard: /var/www/web1)
      -> blast_diamond.pdf (de), blast_diamond_en.pdf (en)

Text in diesem Skript pflegen, nicht im PDF. Jeder Text steht als Paar T("deutsch", "english").
Inline-Markup: `code`, **fett**, __kursiv__. LaTeX-Sonderzeichen werden automatisch maskiert.
Abschnitt 8.5 (Amyloid-Kandidaten) liest die Ergebnisse live aus wagodb.amyl_v_ergebnis."""
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ZIEL = sys.argv[1] if len(sys.argv) > 1 else "/var/www/web1"
DEBUG_START = time.time()


def dbg(msg):
    """Fortschritt mit Zeitstempel und Sekunden seit Start, nach stderr (landet im Log des Aufrufers)."""
    print(f"[{time.strftime('%H:%M:%S')} +{time.time() - DEBUG_START:6.1f}s] {msg}", file=sys.stderr, flush=True)
GENERATOR = f"{socket.gethostname()}:/home/gh/python/blast_diamond_pdf.py"
DATEI = {"de": "blast_diamond.pdf", "en": "blast_diamond_en.pdf"}
STAND = {"de": "23.09.2026", "en": "2026-09-23"}

# ------------------------------------------------------------------ LaTeX-Maskierung

ZEICHEN = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
    "^": r"\textasciicircum{}", "~": r"\textasciitilde{}", "<": r"\textless{}", ">": r"\textgreater{}",
    "|": r"\textbar{}", "→": r"$\rightarrow$", "↔": r"$\leftrightarrow$", "β": r"$\beta$", "α": r"$\alpha$", "×": r"$\times$",
    "·": r"$\cdot$", "≈": r"$\approx$", "≤": r"$\leq$", "≥": r"$\geq$",
}


def esc(s):
    return "".join(ZEICHEN.get(c, c) for c in s)


def esc_code(s):
    """Inline-Code: maskieren und an _ / . - = Umbrueche erlauben (lange Pfade, Tabellennamen)."""
    return "".join(ZEICHEN.get(c, c) + (r"\allowbreak{}" if c in "_/.-=," else "") for c in s)


def md(text):
    codes = []

    def keep(m):
        codes.append(r"\texttt{" + esc_code(m.group(1)) + "}")
        return f"\x00{len(codes) - 1}\x00"

    t = re.sub(r"`([^`]+)`", keep, text)
    t = re.sub(r"\*\*(.+?)\*\*", "\x01\\1\x02", t)
    t = re.sub(r"__(.+?)__", "\x03\\1\x04", t)
    t = esc(t).replace("\x01", r"\textbf{").replace("\x02", "}").replace("\x03", r"\emph{").replace("\x04", "}")
    return re.sub("\x00(\\d+)\x00", lambda m: codes[int(m.group(1))], t)


# ------------------------------------------------------------------ Dokument-Bausteine

class Doku:
    def __init__(self, lang, bilder=None):
        self.lang, self.teile, self.bilder = lang, [], bilder or {}

    def T(self, de, en):
        return de if self.lang == "de" else en

    def h1(self, de, en):
        self.teile.append(r"\section{" + md(self.T(de, en)) + "}")

    def h2(self, de, en):
        self.teile.append(r"\subsection{" + md(self.T(de, en)) + "}")

    def p(self, de, en):
        self.teile.append(md(self.T(de, en)) + "\n")

    def liste(self, punkte):
        items = "\n".join(r"\item " + md(self.T(de, en)) for de, en in punkte)
        self.teile.append(r"\begin{enumerate}" + "\n" + items + "\n" + r"\end{enumerate}")

    def code(self, de, en=None):
        txt = self.T(de, en if en is not None else de).strip("\n")
        self.teile.append(r"\par\noindent\begin{minipage}{\linewidth}\begin{lstlisting}" + "\n" + txt + "\n"
                          + r"\end{lstlisting}\end{minipage}\par")      # Codeblock nie ueber Seitengrenze

    def tabelle(self, kopf, zeilen, breiten):
        """kopf/zeilen: Zellen als (de, en) oder str (sprachneutral); breiten relativ."""
        z = lambda x: md(self.T(*x) if isinstance(x, tuple) else x)   # noqa: E731
        summe = sum(breiten)
        spalten = "".join(r">{\raggedright\arraybackslash}p{\dimexpr" + f"{0.995 * b / summe:.4f}" +
                          r"\linewidth-2\tabcolsep\relax}" for b in breiten)
        kz = " & ".join(r"\textbf{" + z(k) + "}" for k in kopf) + r" \\"
        rumpf = "\n".join(" & ".join(z(c) for c in r) + r" \\" for r in zeilen)
        self.teile.append(
            r"{\small\begin{longtable}{" + spalten + "}\n\\toprule\n" + kz + "\n\\midrule\n\\endhead\n"
            + rumpf + "\n\\bottomrule\n\\end{longtable}}")

    def roh(self, latex):
        self.teile.append(latex)

    def bild(self, name, de, en):
        """ER-Diagramm aus er_diagramme(); fehlt es (DB/Graphviz nicht erreichbar), nur ein Hinweis."""
        if name not in self.bilder:
            self.p("(ER-Diagramm nicht verfügbar: Datenbank oder Graphviz nicht erreichbar.)",
                   "(ER diagram unavailable: database or Graphviz not reachable.)")
            return
        self.teile.append(r"\begin{center}\includegraphics[width=\linewidth,height=0.62\textheight,"
                          r"keepaspectratio]{" + self.bilder[name] + r"}\\[2pt]"
                          r"{\small\color{grau}" + md(self.T(de, en)) + r"}\end{center}")


KOPF = r"""\documentclass[10pt,a4paper]{article}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage[%(babel)s]{babel}
\usepackage{lmodern}
\usepackage[scaled=0.92]{helvet}
\renewcommand{\familydefault}{\sfdefault}
\usepackage{textcomp}
\usepackage[a4paper,margin=2cm,bottom=2.4cm]{geometry}
\usepackage{microtype}
\usepackage{array,longtable,booktabs,graphicx}
\usepackage{xcolor}
\usepackage{enumitem}
\usepackage{listings}
\usepackage{fancyhdr}
\usepackage{titlesec}
\usepackage[hidelinks,bookmarksnumbered]{hyperref}
\hypersetup{pdftitle={%(titel_pdf)s},pdfauthor={gh}}
\definecolor{grau}{HTML}{666666}
\definecolor{code}{HTML}{F4F5F7}
\titleformat{\section}{\Large\bfseries}{\thesection.}{0.5em}{}
\titleformat{\subsection}{\large\bfseries}{\thesubsection}{0.6em}{}
\setlist[enumerate]{itemsep=1pt,topsep=3pt}
\setlength{\parindent}{0pt}
\setlength{\parskip}{5pt}
\setlength{\emergencystretch}{2em}
\setcounter{tocdepth}{1}
\setlength{\LTpre}{4pt}
\setlength{\LTpost}{2pt}
\lstset{basicstyle=\ttfamily\footnotesize,backgroundcolor=\color{code},frame=none,breaklines=true,
  breakatwhitespace=false,columns=fullflexible,keepspaces=true,showstringspaces=false,
  xleftmargin=4pt,framexleftmargin=4pt,aboveskip=4pt,belowskip=6pt,
  literate={…}{{\ldots}}1 {–}{{--}}1 {ä}{{\"a}}1 {ö}{{\"o}}1 {ü}{{\"u}}1 {ß}{{\ss}}1}
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0pt}
\lfoot{\scriptsize\color{grau}%(erzeugt)s \texttt{%(generator)s}}
\rfoot{\scriptsize\color{grau}%(seite)s \thepage}
\fancypagestyle{plain}{\fancyhf{}\lfoot{\scriptsize\color{grau}%(erzeugt)s \texttt{%(generator)s}}\rfoot{\scriptsize\color{grau}%(seite)s \thepage}}
\begin{document}
"""


# ------------------------------------------------------------------ ER-Diagramme aus dem Live-Schema

# Die blast_*-Tabellen haben keine deklarierten Fremdschluessel; ihre Verknuepfungen stehen hier
# (Tabelle, Spalte, Zieltabelle, Zielspalte, Beschriftung). Gezeichnet gestrichelt.
LOGISCH = [
    ("blast_acc", "oid", "blast_seq", "oid", ""),
    ("blast_acc", "taxid", "blast_taxon", "taxid", ""),
    ("blast_taxon", "parent", "blast_taxon", "taxid", ""),
    ("blast_seq_ncbi", "oid", "blast_seq", "oid", ""),
    ("blast_dmnd_luecke", "oid", "blast_seq", "oid", ""),
    ("blast_import", "release_id", "blast_release", "release_id", ""),
    ("blast_import", "first_oid", "blast_seq", "oid", "first_oid..last_oid"),
    ("blast_job", "key_id", "blast_api_key", "key_id", ""),
    ("blast_job", "release_id", "blast_release", "release_id", ""),
    ("blast_job_hit", "job_id", "blast_job", "job_id", ""),
    ("blast_job_hit", "sseqid", "blast_acc", "acc", ""),
    ("amyl_sequenz", "acc", "blast_acc", "acc", ""),
]

# Diagramm: (Dateiname, Tabellen voll, Verweistabellen nur mit den verknuepften Spalten)
DIAGRAMME = {
    "meta": (["blast_seq", "blast_acc", "blast_taxon", "blast_release", "blast_import", "blast_seq_ncbi",
              "blast_dmnd_luecke"], []),
    "api": (["blast_api_key", "blast_job", "blast_job_hit", "blast_api_hits"], ["blast_release", "blast_acc"]),
    "amyl": (["amyl_kandidat", "amyl_sequenz", "amyl_lauf", "amyl_ergebnis", "amyl_fenster"], ["blast_acc"]),
}


def html(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def er_diagramme(c, ziel):
    """Zeichnet DIAGRAMME als PDF nach ziel/er_<name>.pdf; liefert {name: pfad}."""
    c.execute("SELECT table_name, column_name, column_type FROM information_schema.columns "
              "WHERE table_schema = DATABASE() AND (table_name LIKE 'blast%%' OR table_name LIKE 'amyl%%') "
              "ORDER BY table_name, ordinal_position")
    spalten = {}
    for t, col, typ in c.fetchall():
        spalten.setdefault(t, []).append((col, typ.replace(" unsigned", "").replace("(10)", "")
                                          .replace("(5)", "").replace("(20)", "")))
    c.execute("SELECT table_name, column_name, index_name, non_unique FROM information_schema.statistics "
              "WHERE table_schema = DATABASE() AND (table_name LIKE 'blast%%' OR table_name LIKE 'amyl%%')")
    schluessel = {}
    for t, col, idx, nu in c.fetchall():
        art = "PK" if idx == "PRIMARY" else ("UK" if not nu else "IX")
        schluessel.setdefault((t, col), set()).add(art)
    c.execute("SELECT table_name, column_name, referenced_table_name, referenced_column_name "
              "FROM information_schema.key_column_usage WHERE table_schema = DATABASE() "
              "AND referenced_table_name IS NOT NULL AND (table_name LIKE 'blast%%' OR table_name LIKE 'amyl%%')")
    deklariert = [(t, col, rt, rc, "") for t, col, rt, rc in c.fetchall()]

    pfade = {}
    for name, (voll, verweis) in DIAGRAMME.items():
        alle = set(voll) | set(verweis)
        kanten = [(k, True) for k in deklariert if k[0] in alle and k[2] in alle] + \
                 [(k, False) for k in LOGISCH if k[0] in alle and k[2] in alle]
        fk = {(k[0], k[1]) for k, _ in kanten}
        benutzt = {(k[0], k[1]) for k, _ in kanten} | {(k[2], k[3]) for k, _ in kanten}
        dot = ['digraph ER {', 'graph [rankdir=LR, nodesep=0.35, ranksep=0.9, fontname="Helvetica"];',
               'node [shape=plain, fontname="Helvetica", fontsize=10];',
               'edge [fontname="Helvetica", fontsize=8, dir=both, arrowtail=crow, arrowhead=tee, color="#2e3b4e"];']
        for t in voll + verweis:
            if t not in spalten:
                continue
            ref = t in verweis
            kopf_farbe, rand = ("#8a8f98", "#b8bcc4") if ref else ("#2e3b4e", "#2e3b4e")
            zeilen = [f'<TR><TD COLSPAN="3" BGCOLOR="{kopf_farbe}"><FONT COLOR="white"><B>{html(t)}</B></FONT></TD></TR>']
            for col, typ in spalten[t]:
                if ref and (t, col) not in benutzt:
                    continue
                marken = sorted(schluessel.get((t, col), set()) | ({"FK"} if (t, col) in fk else set()),
                                key=["PK", "FK", "UK", "IX"].index)
                mk = " ".join(marken)
                fett = ("<B>", "</B>") if "PK" in marken else ("", "")
                # dot lehnt leere <FONT></FONT> ab: Zelle dann ohne FONT
                mk_td = f'<FONT COLOR="#b0413e">{mk}</FONT>' if mk else " "
                zeilen.append(f'<TR><TD ALIGN="LEFT" PORT="{html(col)}">{fett[0]}{html(col)}{fett[1]}</TD>'
                              f'<TD ALIGN="LEFT"><FONT COLOR="#666666">{html(typ)}</FONT></TD>'
                              f'<TD ALIGN="LEFT">{mk_td}</TD></TR>')
            if ref:
                zeilen.append('<TR><TD COLSPAN="3"><FONT COLOR="#8a8f98" POINT-SIZE="8">…</FONT></TD></TR>')
            dot.append(f'"{t}" [label=<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="3" '
                       f'COLOR="{rand}">{"".join(zeilen)}</TABLE>>];')
        for (t, col, rt, rc, text), echt in kanten:
            stil = "solid" if echt else "dashed"
            lbl = f', label="{text}"' if text else ""
            dot.append(f'"{t}":"{col}" -> "{rt}":"{rc}" [style={stil}{lbl}];')
        dot.append("}")
        pfad = os.path.join(ziel, f"er_{name}.pdf")
        t0 = time.time()
        try:
            r = subprocess.run(["dot", "-Tpdf", "-o", pfad], input="\n".join(dot), text=True,
                               capture_output=True, timeout=120)
        except subprocess.TimeoutExpired:
            dbg(f"ER {name}: dot ueberschritt 120s, Diagramm faellt weg")
            continue
        if r.returncode != 0:
            dbg(f"ER {name}: dot fehlgeschlagen: {r.stderr.strip()[:300]}")
            continue
        dbg(f"ER {name}: {len(voll)} Tabellen, {len(kanten)} Kanten, dot {time.time() - t0:.1f}s")
        pfade[name] = pfad
    return pfade


# ------------------------------------------------------------------ Amyloid-Ergebnisse aus wagodb

def amyl_ergebnis():
    """Letzter vollstaendiger Lauf aus amyl_lauf/amyl_ergebnis, oder None."""
    try:
        sys.path.insert(0, os.path.expanduser("~/python"))
        import blast_db
        import pymysql
        c = pymysql.connect(**blast_db.cfg()).cursor()
        c.execute("SET SESSION max_statement_time = 60")     # nie am PDF-Bau haengen bleiben
        c.execute("SELECT l.lauf_id, l.gestartet, l.sekunden, l.geraet, l.window_size, l.n_seq, COUNT(e.seq_id) "
                  "FROM amyl_lauf l JOIN amyl_ergebnis e USING (lauf_id) GROUP BY l.lauf_id "
                  "HAVING COUNT(e.seq_id) = l.n_seq AND l.n_seq > 1 ORDER BY l.lauf_id DESC LIMIT 1")
        lauf = c.fetchone()
        if not lauf:
            return None
        # Nicht ueber amyl_v_ergebnis: die View holt Titel/Taxon aus blast_acc dazu (im PDF nicht verwendet)
        # und waere ohne idx_acc ein Full Scan ueber blast_acc.
        c.execute(
            "SELECT k.kurz, s.acc, s.laenge, e.avg_prob, e.max_prob, e.max_pos, "
            "       SUBSTRING(s.sequenz, e.max_pos, l.window_size), e.sekunden "
            "FROM amyl_ergebnis e JOIN amyl_lauf l ON l.lauf_id = e.lauf_id "
            "JOIN amyl_sequenz s ON s.seq_id = e.seq_id JOIN amyl_kandidat k ON k.kandidat_id = s.kandidat_id "
            "WHERE e.lauf_id = %s ORDER BY e.max_prob DESC", (lauf[0],))
        seqs = c.fetchall()
        c.execute("SELECT COUNT(*) FROM amyl_fenster WHERE lauf_id = %s", (lauf[0],))
        return {"lauf": lauf, "seqs": seqs, "n_fenster": c.fetchone()[0]}
    except Exception as e:                                   # PDF entsteht auch ohne DB
        print(f"amyl_*: nicht lesbar ({e}), Abschnitt 8.5 ohne Tabelle", file=sys.stderr)
        return None


def spike_test():
    """Ergebnis des Spike-Tests (Abschnitt 8.6) aus wagodb, oder None."""
    try:
        sys.path.insert(0, os.path.expanduser("~/python"))
        import blast_db
        import pymysql
        c = pymysql.connect(**blast_db.cfg()).cursor()
        c.execute("SET SESSION max_statement_time = 60")
        c.execute("SELECT e.lauf_id, s.seq_id, l.window_size, s.laenge FROM amyl_ergebnis e "
                  "JOIN amyl_lauf l ON l.lauf_id = e.lauf_id JOIN amyl_sequenz s ON s.seq_id = e.seq_id "
                  "JOIN amyl_kandidat k ON k.kandidat_id = s.kandidat_id WHERE k.acc_basis = 'YP_009724390' "
                  "ORDER BY e.lauf_id DESC LIMIT 1")
        row = c.fetchone()
        if not row:
            return None
        lauf, seq_id, w, laenge = row
        c.execute("SELECT COUNT(*), SUM(prob >= (SELECT prob FROM amyl_fenster WHERE lauf_id = %s "
                  "AND seq_id = %s AND pos = 194)), (SELECT prob FROM amyl_fenster WHERE lauf_id = %s "
                  "AND seq_id = %s AND pos = 194) FROM amyl_fenster WHERE lauf_id = %s AND seq_id = %s",
                  (lauf, seq_id, lauf, seq_id, lauf, seq_id))
        n_fen, rang, prob = c.fetchone()
        if prob is None:
            return None
        c.execute("SELECT f.pos, SUBSTRING(s.sequenz, f.pos, %s), f.prob FROM amyl_fenster f "
                  "JOIN amyl_sequenz s ON s.seq_id = f.seq_id WHERE f.lauf_id = %s AND f.seq_id = %s "
                  "ORDER BY f.prob DESC LIMIT 5", (w, lauf, seq_id))
        top = c.fetchall()
        c.execute("SELECT COUNT(DISTINCT f.seq_id), COUNT(*), SUM(f.prob >= %s) FROM amyl_fenster f "
                  "JOIN amyl_sequenz s ON s.seq_id = f.seq_id "
                  "JOIN amyl_kandidat k ON k.kandidat_id = s.kandidat_id WHERE k.rolle = 'kontrolle'", (prob,))
        n_prot, n_kfen, n_hoeher = c.fetchone()
        return {"lauf": lauf, "laenge": laenge, "w": w, "n_fen": n_fen, "rang": int(rang), "prob": float(prob),
                "top": top, "n_prot": n_prot, "n_kfen": n_kfen or 0, "n_hoeher": int(n_hoeher or 0)}
    except Exception as e:
        dbg(f"Spike-Test: nicht lesbar ({e})")
        return None


RBD = ("RVQPTESIVRFPNITNLCPFGEVFNATRFASVYAWNRKRISNCVADYSVLYNSASFSTFKCYGVSPTKLNDLCFTNVYADSFVIRGDEVRQIAPGQTGKIADYNY"
       "KLPDDFTGCVIAWNSNNLDSKVGGNYNYLYRLFRKSNLKPFERDISTEIYQAGSTPCNGVEGFNCYFPLQSYGFQPTNGVGYQPYRVVVLSFELLHAPATVCGPKK"
       "STNLVKNKCVNF")


# ------------------------------------------------------------------ Inhalt

def inhalt(d, amyl, spike):
    T = d.T
    d.roh(r"\begin{center}{\LARGE\bfseries " + md(T("BLAST → DIAMOND: Konzept zur Laufzeitoptimierung",
                                                    "BLAST → DIAMOND: a concept for faster searches")) + r"}\\[6pt]"
          + r"{\color{grau}\small " + md(T("Schneller Einzelsequenz-Vergleich einer Virussequenz gegen NCBI nr",
                                            "Fast single-sequence comparison of a viral sequence against NCBI nr"))
          + r"\\" + md(T(f"Stand: {STAND['de']} — dell-3660 (192.168.5.23)",
                         f"As of {STAND['en']} — dell-3660 (192.168.5.23)"))
          + r"\\" + md(T(f"Erzeugt von `{GENERATOR}` → `{ZIEL}/{DATEI['de']}`",
                         f"Generated by `{GENERATOR}` → `{ZIEL}/{DATEI['en']}`"))
          + r"}\end{center}" + "\n" + r"{\small\tableofcontents}\bigskip")

    d.h1("Ausgangslage", "Starting point")
    d.p("blast2.py führt einen blastx-Lauf einer einzelnen SARS-CoV-2-Genomsequenz (~30 kb, 6 Frames) gegen die "
        "vollständige lokale NCBI-nr-Datenbank aus, eingeschränkt per **-taxids 4751** auf Fungi. Beobachtete "
        "Laufzeit: > 20 Minuten für einen einzigen Lauf.",
        "blast2.py runs blastx for a single SARS-CoV-2 genome sequence (~30 kb, 6 frames) against the complete "
        "local NCBI nr database, restricted to fungi with **-taxids 4751**. Observed run time: > 20 minutes for "
        "a single run.")

    d.h1("Analyse der Ursache", "Root cause")
    d.tabelle([("Kennzahl", "Metric"), ("Wert", "Value")], [
        [("Datenbankgröße", "Database size"), ("844 GB, 176 Volumes", "844 GB, 176 volumes")],
        [("Sequenzen in nr", "Sequences in nr"), ("1,15 Mrd.", "1.15 billion")],
        [("Residuen gesamt", "Total residues"), ("435 Mrd.", "435 billion")],
        ["RAM (dell-3660)", "62 GB"],
        [("genutzte Kerne im Skript", "Cores used by the script"), ("8 von 20", "8 of 20")],
        ["GPU", "Tesla P4, 8 GB VRAM, Pascal sm_61"],
    ], [1, 1])
    d.p("Die Datenbank übersteigt den verfügbaren Arbeitsspeicher um mehr als das Zehnfache, der Lauf ist damit "
        "an die Platte gebunden. Der Taxid-Filter reduziert die gemeldeten Treffer, ändert aber nichts an der "
        "nötigen Wortsuche über die vollständige Datenbank. Zusätzlich wird weniger als die Hälfte der CPU-Kerne "
        "genutzt.",
        "The database is more than ten times larger than the available RAM, so the run is disk-I/O bound. The "
        "taxid filter reduces the reported hits but not the word search over the complete database. In "
        "addition, less than half of the CPU cores are used.")

    d.h1("Strategie: DIAMOND statt BLASTX", "Strategy: DIAMOND instead of BLASTX")
    d.p("DIAMOND (Buchfink, Xie & Huson, __Nature Methods__ 2015; Weiterentwicklung Buchfink et al. 2021, "
        "„Sensitive protein alignments at tree-of-life scale“) ist ausdrücklich für den Vergleich einzelner "
        "Sequenzen gegen Datenbanken in nr-Größe entwickelt. Der Kern ist eine Doppel-Indizierung (Query und "
        "Datenbank werden gemeinsam sortiert und geseedet, statt die Datenbank linear je Query zu durchsuchen) "
        "plus Spaced Seeds. Berichtete Beschleunigung gegenüber BLASTX: 100–20.000× bei vergleichbarer "
        "Trefferqualität; die Datenbank muss dabei nicht ins RAM passen (blockweises Streaming).",
        "DIAMOND (Buchfink, Xie & Huson, __Nature Methods__ 2015; extended in Buchfink et al. 2021, “Sensitive "
        "protein alignments at tree-of-life scale”) is built for comparing individual sequences against "
        "databases of nr size. Its core is double indexing (query and database are sorted and seeded together "
        "instead of scanning the database linearly per query) plus spaced seeds. Reported speed-up over BLASTX: "
        "100–20,000× at comparable sensitivity, and the database does not have to fit into RAM (block-wise "
        "streaming).")
    d.p("Dieselbe Grundidee (schneller Alignment-Vorfilter gegen komplette nr/nt zur Identifikation "
        "unbekannter/neuartiger Erreger) steckt hinter CZ-ID / IDseq (Kalantar et al. 2020, __GigaScience__), "
        "einer Pipeline des Chan Zuckerberg Biohub für genau diesen Anwendungsfall.",
        "The same idea (a fast alignment prefilter against complete nr/nt to identify unknown or novel "
        "pathogens) is behind CZ-ID / IDseq (Kalantar et al. 2020, __GigaScience__), a pipeline of the Chan "
        "Zuckerberg Biohub for exactly this use case.")
    d.p("Alternative: MMseqs2 (Steinegger & Söding, __Nature Biotechnology__ 2017) verfolgt denselben "
        "Vorfilter-Gedanken, ist bei höchster Empfindlichkeit etwas langsamer als DIAMOND, dafür teils "
        "treffsicherer bei entfernten Homologien. Eingesetzt wird DIAMOND: geringster Umstellungsaufwand "
        "(Drop-in für blast2.py), kein zusätzlicher Unterbau nötig.",
        "Alternative: MMseqs2 (Steinegger & Söding, __Nature Biotechnology__ 2017) follows the same prefilter "
        "idea; at maximum sensitivity it is slightly slower than DIAMOND but sometimes more accurate for distant "
        "homologues. DIAMOND was chosen: least migration effort (drop-in for blast2.py), no additional "
        "infrastructure.")

    d.h1("Stand", "Status")
    d.p("Statt einer physischen Teil-DB (Fungi) gibt es eine vollständige `nr_full.dmnd` mit Taxonomie-Map; "
        "eingeschränkt wird bei der Suche per `--taxonlist`. Gebaut wird auf sda5 (`/mnt/archive`).",
        "Instead of a physical subset (fungi) there is a complete `nr_full.dmnd` with taxonomy map; searches "
        "are restricted with `--taxonlist`. The build runs on sda5 (`/mnt/archive`).")
    d.p("**Ein Durchgang für .dmnd und Metadaten: `~/python/nr_build.py`** (seit 23.09.2026, ersetzt "
        "`diamond_pipeline.sh`, `filter_nonempty.py`, den Erstimport von `blast_meta2db.py` und "
        "`blast_release_finalize.sh`). Ein einziger `blastdbcmd`-Strom (je Defline OID, Accession, TaxID, Länge, "
        "Sequenz, Titel) wird verteilt: an `makedb` mit Kopfzeilen nur aus Accessions (`>acc1 >acc2 …`, Titel nur "
        "noch in MariaDB), an MariaDB als Chunk-Dateien je 5 Mio. Sequenzen, die ein eigener Thread lädt "
        "(Warteschlange begrenzt, bremst den Strom statt die Platte zu füllen). Leersequenzen gehen nicht in die "
        "Datenbankdatei und werden in `blast_dmnd_luecke` vermerkt. Danach Verifikation beider Ziele gegen "
        "`blastdbcmd -info`, Indizes (tmpdir auf der NVMe), Taxonomie, `blast_release`. Erst dann nimmt die API "
        "Suchen an.",
        "**One pass for .dmnd and metadata: `~/python/nr_build.py`** (since 2026-09-23, replaces "
        "`diamond_pipeline.sh`, `filter_nonempty.py`, the initial import of `blast_meta2db.py` and "
        "`blast_release_finalize.sh`). A single `blastdbcmd` stream (per defline OID, accession, TaxID, length, "
        "sequence, title) is split: to `makedb` with headers made of accessions only (`>acc1 >acc2 …`, titles "
        "live only in MariaDB), and to MariaDB as chunk files of 5 million sequences loaded by a separate thread "
        "(bounded queue: it slows the stream instead of filling the disk). Empty sequences are left out of the "
        "database file and recorded in `blast_dmnd_luecke`. Then both targets are verified against "
        "`blastdbcmd -info`, indexes are built (tmpdir on the NVMe), taxonomy and `blast_release` are updated. "
        "Only then does the API accept searches.")
    d.p("**Speicherfehler im ersten Bau (23.09.2026, 11:03):** `makedb` sammelte die Offset-Tabelle "
        "(16 Byte je Sequenz) in einem `vector` (`dmnd.cpp`), der bei 2^30 Einträgen – Volume ~164 von 176 – seine "
        "Kapazität verdoppelte und kurz ~51 GB brauchte; der OOM-Killer beendete den Bau nach 3,5 h. Patch: die "
        "Einträge gehen in 1-Mio.-Blöcken in `<db>.pos_tmp` und werden am Ende in den Trailer kopiert. Geprüft an "
        "2,5 Mio. nr-Sequenzen: Datei byteweise identisch zum unveränderten Binary, `--append` ebenfalls "
        "identisch. Gebaut wird mit `~/src/diamond/build/diamond` (v2.2.8 + `--append` + dieser Patch), dem "
        "Binary, das auch die Cron-Skripte nutzen.",
        "**Memory failure in the first build (2026-09-23, 11:03):** `makedb` collected the offset table (16 "
        "bytes per sequence) in a `vector` (`dmnd.cpp`) that doubled its capacity at 2^30 entries – volume "
        "~164 of 176 – and briefly needed ~51 GB; the OOM killer ended the build after 3.5 h. Patch: entries "
        "go to `<db>.pos_tmp` in blocks of one million and are copied into the trailer at the end. Checked on "
        "2.5 million nr sequences: file byte-identical to the unmodified binary, `--append` identical as well. "
        "Builds use `~/src/diamond/build/diamond` (v2.2.8 + `--append` + this patch), the same binary the cron "
        "scripts use.")
    d.p("Die BLAST-formatierte nr in `~/blastdb` wird derzeit noch gebraucht: API (`acc`, Abschnitt 7.5) und "
        "AmyloDeep (`--acc`/`--kandidaten`, Abschnitt 8) holen Residuen per `blastdbcmd` daraus. Wie diese "
        "doppelte Datenhaltung aufgelöst wird, beschreibt Abschnitt 9.",
        "The BLAST-formatted nr in `~/blastdb` is still needed for now: the API (`acc`, section 7.5) and "
        "AmyloDeep (`--acc`/`--kandidaten`, section 8) fetch residues from it with `blastdbcmd`. Section 9 "
        "describes how to remove this duplicate storage.")

    d.h1("Append: neue Sequenzen ohne Neubau", "Append: new sequences without a rebuild")
    d.p("NCBI bietet für die BLAST-formatierte nr kein inkrementelles Update an (README blast/db: „Incremental "
        "update is not available“), und DIAMOND kann an eine bestehende .dmnd nichts anhängen. Beides wurde "
        "ergänzt.",
        "NCBI offers no incremental update for the BLAST-formatted nr (README blast/db: “Incremental update is "
        "not available”), and DIAMOND cannot append to an existing .dmnd. Both were added.")

    d.h2("Patch `diamond makedb --append`", "Patch `diamond makedb --append`")
    d.p("Branch `makedb-append` in `~/src/diamond`, Diff in `~/src/diamond-makedb-append.patch` (3 Dateien, ~140 "
        "Zeilen), Binary `~/src/diamond/build/diamond` (v2.2.8 + Patch). Aufbau einer .dmnd: Header, Sequenzen, "
        "danach der Trailer aus Offset-Tabelle, Taxon-Listen (varint-kodiert je OID, ohne eigenen Index), "
        "Taxonomie-Knoten und Namen; der Header wird zuletzt per `seek(0)` geschrieben. Ablauf von `--append`:",
        "Branch `makedb-append` in `~/src/diamond`, diff in `~/src/diamond-makedb-append.patch` (3 files, ~140 "
        "lines), binary `~/src/diamond/build/diamond` (v2.2.8 + patch). Layout of a .dmnd: header, sequences, "
        "then the trailer with offset table, taxon lists (varint-encoded per OID, no index of their own), "
        "taxonomy nodes and names; the header is written last via `seek(0)`. What `--append` does:")
    d.liste([
        ("Header lesen und prüfen (Magic, Version 3, gleiche --taxonmap/--taxonnodes/--taxonnames-Nutzung wie beim "
         "Erstbau).",
         "Read and check the header (magic, version 3, same use of --taxonmap/--taxonnodes/--taxonnames as the "
         "initial build)."),
        ("Header und kompletten Trailer in `<db>.append_backup` sichern (bei nr ca. 20–25 GB).",
         "Back up header and complete trailer to `<db>.append_backup` (about 20–25 GB for nr)."),
        ("Datei am Ende der Sequenzen abschneiden (`pos_array_offset`), neue Sequenzen dahinter schreiben. Der "
         "fortlaufende MurmurHash3 läuft vom gespeicherten Wert weiter.",
         "Truncate the file at the end of the sequences (`pos_array_offset`) and write the new sequences after "
         "them. The running MurmurHash3 continues from the stored value."),
        ("Trailer neu schreiben: alte Offset-Einträge + neue, alte Taxon-Listen byteweise + neue "
         "(`TaxonList::build` nur für die neuen OIDs), Taxonomie aus dem aktuellen taxdump.",
         "Rewrite the trailer: old offset entries + new ones, old taxon lists byte for byte + new ones "
         "(`TaxonList::build` only for the new OIDs), taxonomy from the current taxdump."),
        ("Header schreiben, Backup löschen. Bei jedem Fehler stellt der Patch Header und Trailer aus dem Backup "
         "wieder her.",
         "Write the header, delete the backup. On any error the patch restores header and trailer from the "
         "backup."),
    ])
    d.p("**Tests (23.09.2026):** (1) DB aus A (12.000 Sequenzen) plus `--append` von B (8.000) ist **byteweise "
        "identisch** mit der in einem Durchgang gebauten DB aus A+B (`cmp`), inkl. Hash, Taxon-Listen und Header. "
        "(2) Append mit einer Sequenz der Länge 0 bricht ab, die DB ist danach byteweise identisch zum Stand "
        "vorher, kein Backup-Rest. (3) `diamond blastp` mit Ausgabe `staxids` auf der erweiterten DB liefert "
        "korrekte Treffer.",
        "**Tests (2026-09-23):** (1) a DB built from A (12,000 sequences) plus `--append` of B (8,000) is "
        "**byte-identical** to the DB built from A+B in one pass (`cmp`), including hash, taxon lists and "
        "header. (2) Appending a sequence of length 0 aborts; the DB is byte-identical to its previous state, "
        "no backup left over. (3) `diamond blastp` with `staxids` output on the extended DB returns correct "
        "hits.")

    d.h2("Update-Skript `diamond_append.py`", "Update script `diamond_append.py`")
    d.p("Erkennung eines neuen Releases: `nr-prot-metadata.json` von NCBI (`last-updated`, `number-of-sequences`) "
        "gegen den neuesten Eintrag in `blast_release`; gleich = sofort Ende. Bei neuem Release: Volume für "
        "Volume laden (md5 geprüft), entpacken, Accessions gegen ein sortiertes 64-bit-Hash-Array aller "
        "bekannten Accessions (~15 GB, per Memory-Map) prüfen, Volume löschen. Spitzenbedarf ein Volume statt "
        "855 GB. Neu ist eine Sequenz, wenn keine ihrer Accessions bekannt ist. Danach genau ein `--append` "
        "(Zustandsdatei verhindert doppeltes Anhängen), Metadaten in `blast_seq`/`blast_acc` mit neuen OIDs ab "
        "dem bisherigen Maximum, neue Zeile in `blast_release`, Mail an gh@heissa.de. Fortsetzbar je Volume. Wann "
        "eine Sequenz hinzukam, liefern die Views `blast_seq_herkunft`/`blast_acc_herkunft` (OID-Bereich in "
        "`blast_import` → Release).",
        "Detecting a new release: NCBI's `nr-prot-metadata.json` (`last-updated`, `number-of-sequences`) is "
        "compared with the newest row in `blast_release`; equal means done. For a new release: download volume "
        "by volume (md5-checked), unpack, check accessions against a sorted 64-bit hash array of all known "
        "accessions (~15 GB, memory-mapped), delete the volume. Peak disk use is one volume instead of 855 GB. "
        "A sequence is new if none of its accessions is known. Then exactly one `--append` (a state file "
        "prevents appending twice), metadata in `blast_seq`/`blast_acc` with new OIDs from the previous "
        "maximum, a new row in `blast_release`, mail to gh@heissa.de. Resumable per volume. When a sequence was "
        "added is available from the views `blast_seq_herkunft`/`blast_acc_herkunft` (OID range in "
        "`blast_import` → release).")
    d.p("**Ende-zu-Ende-Test** mit Testtabellen `blasttest_*`: Bestand A, „neues Release“ = Volume nr.000 bis zur "
        "20.000. Sequenz. Ergebnis: genau 8.000 neue Sequenzen erkannt, .dmnd danach byteweise identisch zur "
        "Voll-DB, Metadaten per Prüfsumme identisch zur Produktion, zweiter Lauf erkennt „kein neues Release“.",
        "**End-to-end test** with test tables `blasttest_*`: existing set A, “new release” = volume nr.000 up to "
        "sequence 20,000. Result: exactly 8,000 new sequences detected, .dmnd byte-identical to the full DB "
        "afterwards, metadata checksum-identical to production, a second run reports “no new release”.")
    d.p("**Grenzen:** neue Accessions an bereits vorhandenen Sequenzen (neuer Organismus für dieselbe Sequenz) und "
        "von NCBI zurückgezogene Sequenzen werden nur gezählt und erst bei einem Vollneubau berücksichtigt. "
        "64-bit-Hash: rechnerisch ~0,2 fälschlich als bekannt eingestufte Accessions je Release-Abgleich.",
        "**Limits:** new accessions on existing sequences (a new organism for the same sequence) and sequences "
        "withdrawn by NCBI are only counted and handled at the next full rebuild. 64-bit hash: an expected "
        "~0.2 accessions per release comparison are wrongly classified as known.")
    d.p("Sequenzen, die bereits per Tagesdelta (5.3) angehängt wurden, erkennt das Skript am Sequenz-Hash und "
        "hängt sie nicht ein zweites Mal an (Zähler „schon per Tagesdelta“). Der MariaDB-Teil läuft in einer "
        "Transaktion (6.4). Test mit Testtabellen: 3.000 Sequenzen, davon 1.000 schon in nr (Accession) und 10 "
        "schon per Tagesdelta (Sequenz) → genau 1.990 angehängt, alte Release-Zeilen unverändert.",
        "Sequences already appended by the daily delta (5.3) are recognised by their sequence hash and not "
        "appended a second time (counter “already via daily delta”). The MariaDB part runs in one transaction "
        "(6.4). Test with test tables: 3,000 sequences, 1,000 of them already in nr (accession) and 10 already "
        "via daily delta (sequence) → exactly 1,990 appended, old release rows unchanged.")

    d.h2("Tagesdelta `nr_daily_delta.py`", "Daily delta `nr_daily_delta.py`")
    d.p("Die BLAST-nr wird von NCBI nur als Ganzes neu erzeugt (letzter Stand 16.09.2026, `blast/db/FASTA/nr.gz` "
        "seit 06.02.2024 eingefroren). Zwischen zwei Releases kommen neue Proteine aus den Tagesdateien: "
        "`genbank/daily-nc/ncMMDD.gnp.gz` (GenPept, ~180 Tage vorgehalten) und "
        "`refseq/daily/rsnc.MMDD.YYYY.gpff.gz` (~21 Tage). Das GenPept-Format enthält Accession, Titel, TaxID "
        "(`/db_xref=\"taxon:`), Sequenz und in der LOCUS-Zeile das NCBI-Datum.",
        "NCBI rebuilds the BLAST nr only as a whole (latest 2026-09-16; `blast/db/FASTA/nr.gz` frozen since "
        "2024-02-06). Between releases, new proteins come from the daily files: "
        "`genbank/daily-nc/ncMMDD.gnp.gz` (GenPept, kept ~180 days) and `refseq/daily/rsnc.MMDD.YYYY.gpff.gz` "
        "(~21 days). GenPept contains accession, title, TaxID (`/db_xref=\"taxon:`), sequence and, in the LOCUS "
        "line, the NCBI date.")
    d.p("**Duplikate nach Sequenz, nicht nach Accession:** nr fasst identische Proteine zu einem Eintrag zusammen, "
        "führt dabei aber nicht jede Accession auf (Beispiel: YGQ27362.1 ist sequenzgleich zu YP_009724396.1, "
        "dessen nr-Eintrag nur 7 Deflines hat). Ein Abgleich nach Accession würde also massenhaft Duplikate "
        "anhängen. Deshalb: blake2b-64-Hash jeder Sequenz (Großbuchstaben) gegen das sortierte Hash-Array aller "
        "nr-Sequenzen (`/mnt/archive/nr_seq_hashes.npy`, 9,2 GB, per Memory-Map) plus die im selben Lauf schon "
        "gesehenen. Alle offenen Tagesdateien werden in **einem** `--append` verarbeitet, die TaxID-Zuordnung "
        "kommt als kleine Mapping-Datei aus dem GenPept selbst.",
        "**Duplicates by sequence, not by accession:** nr merges identical proteins into one entry but does not "
        "list every accession (example: YGQ27362.1 has the same sequence as YP_009724396.1, whose nr entry has "
        "only 7 deflines). Matching by accession would therefore append duplicates en masse. Instead: a "
        "blake2b-64 hash of every sequence (upper case) is checked against the sorted hash array of all nr "
        "sequences (`/mnt/archive/nr_seq_hashes.npy`, 9.2 GB, memory-mapped) plus those already seen in the same "
        "run. All pending daily files are processed in **one** `--append`; the TaxID mapping comes as a small "
        "mapping file from the GenPept itself.")
    d.p("**Test (23.09.2026, Testtabellen, Test-dmnd):** nc0919 (19.09.): 5.189 Proteine, 100 schon in der "
        "Test-„nr“, 3.195 innerhalb der Datei doppelt, 1.894 neu; nc0920: 7.104 Proteine, 6.491 neu. dmnd danach "
        "100 + 8.385 Sequenzen, OIDs in MariaDB lückenlos, jede neue Sequenz mit NCBI-Datum; zweiter Lauf: „Keine "
        "offenen Tagesdateien“. Beispiel einer neuen humanen Sequenz: BIN93201.1 __spexin precursor, partial "
        "[Homo sapiens]__, NCBI-Datum 19.09.2026.",
        "**Test (2026-09-23, test tables, test dmnd):** nc0919 (Sep 19): 5,189 proteins, 100 already in the test "
        "“nr”, 3,195 duplicated within the file, 1,894 new; nc0920: 7,104 proteins, 6,491 new. The dmnd then held "
        "100 + 8,385 sequences, OIDs in MariaDB without gaps, every new sequence with its NCBI date; a second run "
        "reports “no pending daily files”. Example of a new human sequence: BIN93201.1 __spexin precursor, "
        "partial [Homo sapiens]__, NCBI date 2026-09-19.")
    d.p("**Cron:** 03:30 `diamond_append.py` (neues nr-Release), 04:30 `nr_daily_delta.py` (Tagesdateien). Beide "
        "nehmen dieselbe Sperre `/home/gh/.nr_dmnd.lock` und warten, bis der Erstaufbau (`dmnd_hash` und "
        "`meta_importiert` in `blast_release`) abgeschlossen ist. Nach einem neuen nr-Release beginnt das "
        "Tagesdelta ab dessen Datum; die zwischenzeitlich per Delta angehängten Sequenzen überspringt der "
        "Release-Abgleich per Sequenz-Hash.",
        "**Cron:** 03:30 `diamond_append.py` (new nr release), 04:30 `nr_daily_delta.py` (daily files). Both take "
        "the same lock `/home/gh/.nr_dmnd.lock` and wait until the initial build (`dmnd_hash` and "
        "`meta_importiert` in `blast_release`) is complete. After a new nr release the daily delta starts from "
        "its date; sequences appended by the delta in the meantime are skipped by the release comparison via "
        "the sequence hash.")

    d.h1("Metadaten in MariaDB (wagodb, Präfix `blast_`)", "Metadata in MariaDB (wagodb, prefix `blast_`)")
    d.p("Die .dmnd-Datei kennt nur Sequenzen, Accessions als Namen und TaxIDs – kein Datum, keinen NCBI-Stand, "
        "keine Herkunft. Auch die BLAST-nr enthält kein Einreichungsdatum. Fragen wie „welcher nr-Stand ist "
        "geladen“, „seit wann ist diese Sequenz in der DB“ oder „wann kam die letzte humane Sequenz hinzu“ lassen "
        "sich deshalb nur über die Metadaten in MariaDB beantworten. Sie sind zugleich die Grundlage der API "
        "(Treffer aus DIAMOND per Accession mit Titel, Organismus und Taxonomie anreichern). Jedes Skript, das "
        "die .dmnd verändert, pflegt im selben Lauf auch diese Tabellen.",
        "The .dmnd file only knows sequences, accessions as names and TaxIDs – no date, no NCBI release, no "
        "provenance. The BLAST nr has no submission date either. Questions like “which nr release is loaded”, "
        "“since when is this sequence in the DB” or “when was the last human sequence added” can therefore only "
        "be answered from the metadata in MariaDB. They are also the basis of the API (DIAMOND hits enriched by "
        "accession with title, organism and taxonomy). Every script that changes the .dmnd updates these tables "
        "in the same run.")

    d.h2("Tabellen", "Tables")
    d.tabelle([("Tabelle", "Table"), ("Zeilen (nr 09/2026)", "Rows (nr 09/2026)"), ("Inhalt", "Content")], [
        ["blast_seq", ("1,15 Mrd.", "1.15 billion"),
         ("je Sequenz: oid (PK), Länge, Zahl der Accessions, Titel der ersten Defline",
          "per sequence: oid (PK), length, number of accessions, title of the first defline")],
        ["blast_acc", ("~1,94 Mrd.", "~1.94 billion"),
         ("je Defline: oid, pos, acc, taxid; Index auf acc und taxid",
          "per defline: oid, pos, acc, taxid; indexes on acc and taxid")],
        ["blast_taxon", ("~2,7 Mio.", "~2.7 million"),
         ("NCBI-Taxonomie: taxid, parent, rank, wissenschaftlicher Name",
          "NCBI taxonomy: taxid, parent, rank, scientific name")],
        ["blast_release", ("1 + 1 je Tagesdatei", "1 + 1 per daily file"),
         ("Stand: quelle (nr / genbank-daily / refseq-daily), datei, nr_datum, Sequenzen, dmnd_hash, aktiv",
          "release: quelle (nr / genbank-daily / refseq-daily), datei, nr_datum, sequences, dmnd_hash, aktiv")],
        ["blast_import", ("231 + 1 je Delta", "231 + 1 per delta"),
         ("OID-Bereich first_oid..last_oid → release_id, Zeitpunkt geladen",
          "OID range first_oid..last_oid → release_id, load time")],
        ["blast_seq_ncbi", ("nur Delta-Sequenzen", "delta sequences only"),
         ("oid → NCBI-Datum aus der GenPept-LOCUS-Zeile", "oid → NCBI date from the GenPept LOCUS line")],
    ], [3.3, 3.3, 10.4])
    d.p("blast_seq und blast_acc sind InnoDB `ROW_FORMAT=COMPRESSED`. Beim Import-Zwischenstand (140 Mio. "
        "Sequenzen) belegen beide je 6,3 GB; hochgerechnet auf die ganze nr je ~52 GB, dazu die beiden Indizes "
        "auf blast_acc. Alles liegt auf der System-NVMe (`/var/lib/mariadb`).",
        "blast_seq and blast_acc are InnoDB `ROW_FORMAT=COMPRESSED`. At an intermediate import state (140 million "
        "sequences) each used 6.3 GB; extrapolated to all of nr ~52 GB each, plus the two indexes on blast_acc. "
        "Everything lives on the system NVMe (`/var/lib/mariadb`).")
    d.bild("meta", "ER-Diagramm der nr-Metadaten, erzeugt aus dem Live-Schema (information_schema). Durchgezogen: deklarierte Fremdschlüssel, gestrichelt: logische Verknüpfungen; Krähenfuß = viele. PK/UK/IX/FK wie in der Datenbank; Indizes auf blast_acc erscheinen, sobald `nr_build.py` sie gebaut hat.",
           "ER diagram of the nr metadata, generated from the live schema (information_schema). Solid: declared foreign keys, dashed: logical links; crow's foot = many. PK/UK/IX/FK as in the database; indexes on blast_acc appear once `nr_build.py` has built them.")

    d.h2("Herkunft und Datum je Sequenz", "Provenance and date per sequence")
    d.p("Jede Sequenz gehört über ihren OID-Bereich in `blast_import` zu genau einer Zeile in `blast_release`. "
        "Die Views `blast_seq_herkunft` und `blast_acc_herkunft` lösen das auf: quelle, datei, nr_stand, "
        "ncbi_datum, hinzugefuegt (Zeitpunkt des Imports). Grenze: alle Sequenzen des Erstimports tragen nur den "
        "nr-Stand 16.09.2026, denn die BLAST-nr liefert kein Einreichungsdatum. Ein echtes Tagesdatum haben erst "
        "die Sequenzen aus dem Tagesdelta (`ncbi_datum` aus der LOCUS-Zeile).",
        "Through its OID range in `blast_import`, every sequence belongs to exactly one row in `blast_release`. "
        "The views `blast_seq_herkunft` and `blast_acc_herkunft` resolve this: quelle, datei, nr_stand, "
        "ncbi_datum, hinzugefuegt (import time). Limit: all sequences of the initial import only carry the nr "
        "release 2026-09-16, because the BLAST nr has no submission date. Only sequences from the daily delta "
        "have a real date (`ncbi_datum` from the LOCUS line).")
    d.p("**OID-Nummern:** die OIDs in MariaDB sind die BLAST-OIDs, fortgezählt für angehängte Sequenzen. Sie "
        "stimmen nicht mit der Satznummer in der .dmnd überein (leere Sequenzen beim Bau verworfen). Verknüpft "
        "wird deshalb über die Accession (DIAMOND-Ausgabe `sseqid` → `blast_acc.acc`).",
        "**OID numbers:** the OIDs in MariaDB are the BLAST OIDs, continued for appended sequences. They do not "
        "match the record number in the .dmnd (empty sequences were dropped during the build). The link is "
        "therefore the accession (DIAMOND output `sseqid` → `blast_acc.acc`).")

    d.h2("Beispielabfragen", "Example queries")
    d.code("""
-- welcher Stand ist geladen?
SELECT quelle, datei, nr_datum, nr_sequenzen, dmnd_hash
FROM blast_release ORDER BY nr_datum DESC LIMIT 5;

-- wann kam die letzte humane Sequenz hinzu?
SELECT acc, quelle, datei, ncbi_datum, hinzugefuegt
FROM blast_acc_herkunft WHERE taxid = 9606
ORDER BY hinzugefuegt DESC, ncbi_datum DESC LIMIT 1;

-- neue Sequenzen je Tag
SELECT n.ncbi_datum, COUNT(*) FROM blast_seq_ncbi n GROUP BY 1 ORDER BY 1 DESC;

-- DIAMOND-Treffer anreichern
SELECT a.acc, s.title, t.name FROM blast_acc a
JOIN blast_seq s USING (oid) JOIN blast_taxon t ON t.taxid = a.taxid
WHERE a.acc = 'YP_009724390.1';
""", """
-- which release is loaded?
SELECT quelle, datei, nr_datum, nr_sequenzen, dmnd_hash
FROM blast_release ORDER BY nr_datum DESC LIMIT 5;

-- when was the last human sequence added?
SELECT acc, quelle, datei, ncbi_datum, hinzugefuegt
FROM blast_acc_herkunft WHERE taxid = 9606
ORDER BY hinzugefuegt DESC, ncbi_datum DESC LIMIT 1;

-- new sequences per day
SELECT n.ncbi_datum, COUNT(*) FROM blast_seq_ncbi n GROUP BY 1 ORDER BY 1 DESC;

-- enrich a DIAMOND hit
SELECT a.acc, s.title, t.name FROM blast_acc a
JOIN blast_seq s USING (oid) JOIN blast_taxon t ON t.taxid = a.taxid
WHERE a.acc = 'YP_009724390.1';
""")

    d.h2("Wer schreibt was", "Who writes what")
    d.tabelle([("Skript", "Script"), ("Wann", "When"), ("Schreibt", "Writes")], [
        ["blast_meta2db.py", ("einmalig (Erstaufbau)", "once (initial build)"),
         ("blast_seq, blast_acc in 231 Stücken zu je 5 Mio. Sequenzen (LOAD DATA LOCAL, ~95 s je Stück, "
          "fortsetzbar über blast_import), danach Indizes und blast_taxon",
          "blast_seq, blast_acc in 231 chunks of 5 million sequences (LOAD DATA LOCAL, ~95 s per chunk, "
          "resumable via blast_import), then indexes and blast_taxon")],
        ["blast_release_finalize.sh", ("nach Build + Import", "after build + import"),
         ("dmnd_hash, dmnd_sequenzen, meta_importiert für den Erststand",
          "dmnd_hash, dmnd_sequenzen, meta_importiert for the initial release")],
        ["diamond_append.py", ("Cron 03:30, neues nr-Release", "cron 03:30, new nr release"),
         ("neue blast_release-Zeile (quelle nr), OID-Bereich, blast_seq/blast_acc der neuen Sequenzen",
          "new blast_release row (quelle nr), OID range, blast_seq/blast_acc of the new sequences")],
        ["nr_daily_delta.py", ("Cron 04:30, Tagesdateien", "cron 04:30, daily files"),
         ("je Tagesdatei eine blast_release-Zeile, OID-Bereich, blast_seq/blast_acc/blast_seq_ncbi",
          "one blast_release row per daily file, OID range, blast_seq/blast_acc/blast_seq_ncbi")],
    ], [4.0, 3.3, 9.7])
    d.p("**Konsistenz .dmnd ↔ MariaDB:** die Append-Skripte hängen erst an die .dmnd an und merken sich das in "
        "einer Zustandsdatei (`dmnd_done`, Hash, Sequenzzahl). Danach schreiben sie alle Metadaten in **einer** "
        "Transaktion. Bricht dieser Teil ab, rollt MariaDB vollständig zurück; der nächste Lauf hängt nicht "
        "erneut an, er trägt nur die Metadaten nach. Getestet: bei absichtlich fehlender Tabelle blieb MariaDB auf "
        "dem alten Stand, die .dmnd wurde nicht doppelt erweitert (Hash unverändert), der Nachlauf ergänzte genau "
        "die fehlenden 8.385 Sequenzen. Die Hash-Arrays (Accessions, Sequenzen) werden erst nach dem Commit "
        "fortgeschrieben.",
        "**Consistency .dmnd ↔ MariaDB:** the append scripts first append to the .dmnd and record that in a "
        "state file (`dmnd_done`, hash, sequence count). Then they write all metadata in **one** transaction. If "
        "that part fails, MariaDB rolls back completely; the next run does not append again but only adds the "
        "missing metadata. Tested: with a deliberately missing table, MariaDB stayed at the old state, the .dmnd "
        "was not extended twice (hash unchanged), and the follow-up run added exactly the missing 8,385 "
        "sequences. The hash arrays (accessions, sequences) are only updated after the commit.")
    d.p("**Zugangsdaten:** alle BLAST-Skripte holen sie über `~/python/blast_db.py` (liest DB_CFG aus "
        "`~/mqtt-listener.py`, Datenbank wagodb).",
        "**Credentials:** all BLAST scripts get them via `~/python/blast_db.py` (reads DB_CFG from "
        "`~/mqtt-listener.py`, database wagodb).")

    d.h1("Öffentliche API", "Public API")
    d.p("Damit auch andere die DIAMOND-Suche und die Metadaten nutzen können, gibt es eine minimale JSON-API: "
        "`https://yt.heissa.de/blast/api.php`. yt.heissa.de hat nur einen AAAA-Eintrag, die API ist von außen "
        "also **nur über IPv6** erreichbar (von heissa.de aus getestet). Ohne Parameter liefert sie ihre eigene "
        "Beschreibung.",
        "So that others can use the DIAMOND search and the metadata, there is a minimal JSON API: "
        "`https://yt.heissa.de/blast/api.php`. yt.heissa.de only has an AAAA record, so from outside the API is "
        "reachable **over IPv6 only** (tested from heissa.de). Without parameters it returns its own "
        "description.")

    d.h2("Endpunkte", "Endpoints")
    d.tabelle([("Aufruf", "Call"), ("Zugang", "Access"), ("Liefert", "Returns")], [
        ["GET ?r=release", ("frei", "open"),
         ("geladener nr-Stand, Tagesdeltas seitdem, Bereitschaft von Suche und Metadaten, Warteschlange",
          "loaded nr release, daily deltas since, readiness of search and metadata, queue")],
        ["GET ?r=acc&acc=YP_009724390.1", ("frei", "open"),
         ("Titel, Länge, Herkunft/Datum der Sequenz, TaxID mit Namen, alle identischen Einträge (bis 200). Ohne "
          ".Version: neueste Version",
          "title, length, provenance/date of the sequence, TaxID with name, all identical entries (up to 200). "
          "Without .version: latest version")],
        [("GET ?r=taxon&taxid=9606 bzw. &name=Homo sapiens", "GET ?r=taxon&taxid=9606 or &name=Homo sapiens"),
         ("frei", "open"), ("Taxon mit vollständiger Abstammungslinie", "taxon with its complete lineage")],
        ["POST ?r=search", "X-Api-Key",
         ("legt einen Suchauftrag an (Query per seq oder acc); Antwort 202 mit job_id, Position und Ergebnis-URL",
          "creates a search job (query via seq or acc); response 202 with job_id, position and result URL")],
        ["GET ?r=job&id=…", ("wer die id kennt", "anyone with the id"),
         ("Status, Parameter, Laufzeit, nr-Stand/dmnd_hash der Suche; wenn fertig die Treffer mit Titel und "
          "Taxon-Namen aus MariaDB",
          "status, parameters, run time, nr release/dmnd_hash of the search; when done, the hits with title and "
          "taxon names from MariaDB")],
    ], [5.2, 2.4, 9.4])
    d.p("**Suchparameter** (Formular oder JSON): `seq` (FASTA oder rohe Sequenz, bis 50 Sequenzen / 100.000 "
        "Buchstaben) **oder** `acc` (Accession aus nr, optional mit `range` von-bis, nur blastp; siehe 7.5), "
        "`mode` blastp oder blastx, `taxonlist` (bis 20 TaxIDs, z.B. 4751 = Fungi, 9606 = Mensch), `evalue` "
        "(Standard 0,001), `max_target_seqs` (1–500, Standard 25), `sensitivity` fast bis ultra-sensitive "
        "(Standard sensitive).",
        "**Search parameters** (form or JSON): `seq` (FASTA or raw sequence, up to 50 sequences / 100,000 "
        "letters) **or** `acc` (accession from nr, optionally with `range` from-to, blastp only; see 7.5), "
        "`mode` blastp or blastx, `taxonlist` (up to 20 TaxIDs, e.g. 4751 = fungi, 9606 = human), `evalue` "
        "(default 0.001), `max_target_seqs` (1–500, default 25), `sensitivity` fast to ultra-sensitive (default "
        "sensitive).")
    d.code("""
curl -X POST -H 'X-Api-Key: …' -H 'Content-Type: application/json' \\
  -d '{"seq":">q1\\nMKT…","mode":"blastp","taxonlist":"9606"}' \\
  'https://yt.heissa.de/blast/api.php?r=search'

curl 'https://yt.heissa.de/blast/api.php?r=job&id=18a8ceae9fcc7d63063e5ffc'
""")

    d.h2("Aufbau", "Architecture")
    d.p("**api.php** (`/var/www/blast`, mod_php, Alias im vHost yt.heissa.de vor dem Invidious-Proxy) beantwortet "
        "Metadaten-Anfragen direkt und legt Suchen nur als Zeile in `blast_job` an. Bei `acc` holt sie die "
        "Query-Sequenz vorher per `blastdbcmd -entry … -target_only [-range …]` aus der BLAST-nr (`~/blastdb/nr`, "
        "~0,3 s). Eine DIAMOND-Suche gegen die volle nr belegt alle 20 Kerne für Minuten und darf deshalb nie im "
        "Webserver-Prozess laufen. **blast_api_worker.py** (systemd `blast-api-worker`, Benutzer gh) holt die "
        "Aufträge nacheinander, ruft DIAMOND auf (Zeitlimit 1 h), schreibt die Treffer in `blast_job_hit` und "
        "vermerkt nr-Stand und dmnd_hash. Während der Suche hält er eine geteilte Sperre auf "
        "`/home/gh/.nr_dmnd.lock`; die Append-Skripte brauchen die exklusive Sperre und verändern die .dmnd "
        "daher nie unter einer laufenden Suche. Nach einem Absturz setzt er hängende Aufträge wieder auf queued. "
        "Solange `dmnd_hash` im aktiven Release fehlt (Erstaufbau), nimmt die API keine Suchen an (503).",
        "**api.php** (`/var/www/blast`, mod_php, alias in the yt.heissa.de vhost in front of the Invidious "
        "proxy) answers metadata requests directly and only records searches as a row in `blast_job`. For `acc` "
        "it first fetches the query sequence with `blastdbcmd -entry … -target_only [-range …]` from the BLAST "
        "nr (`~/blastdb/nr`, ~0.3 s). A DIAMOND search against full nr occupies all 20 cores for minutes and "
        "must never run inside the web server process. **blast_api_worker.py** (systemd `blast-api-worker`, "
        "user gh) takes the jobs one by one, runs DIAMOND (time limit 1 h), writes the hits to `blast_job_hit` "
        "and records nr release and dmnd_hash. During a search it holds a shared lock on "
        "`/home/gh/.nr_dmnd.lock`; the append scripts need the exclusive lock and therefore never change the "
        ".dmnd under a running search. After a crash it resets stuck jobs to queued. As long as `dmnd_hash` is "
        "missing in the active release (initial build), the API rejects searches (503).")
    d.tabelle([("Tabelle", "Table"), ("Inhalt", "Content")], [
        ["blast_api_key", ("Schlüssel nur als sha256, Name, E-Mail, Tageskontingent, aktiv",
                           "key as sha256 only, name, e-mail, daily quota, active")],
        ["blast_job", ("Auftrag: Parameter, Query, Status, Zeiten, Laufzeit, release_id, dmnd_hash, Trefferzahl, "
                       "Fehler",
                       "job: parameters, query, status, timestamps, run time, release_id, dmnd_hash, hit count, "
                       "error")],
        ["blast_job_hit", ("Treffer im DIAMOND-Format 6 (qseqid … bitscore, staxids)",
                           "hits in DIAMOND format 6 (qseqid … bitscore, staxids)")],
        ["blast_api_hits", ("Zähler je IP bzw. IPv6-/64 und Minute (Ratenbegrenzung), vom Worker aufgeräumt",
                            "counter per IP or IPv6 /64 and minute (rate limit), cleaned up by the worker")],
    ], [3.3, 13.7])
    d.bild("api", "ER-Diagramm der API (grau: Tabellen aus Abschnitt 6, nur verknüpfte Spalten), erzeugt aus dem Live-Schema (information_schema). Durchgezogen: deklarierte Fremdschlüssel, gestrichelt: logische Verknüpfungen; Krähenfuß = viele. PK/UK/IX/FK wie in der Datenbank.",
           "ER diagram of the API (grey: tables from section 6, linked columns only), generated from the live schema (information_schema). Solid: declared foreign keys, dashed: logical links; crow's foot = many. PK/UK/IX/FK as in the database.")

    d.h2("Schutz", "Protection")
    d.p("Eigener MariaDB-Benutzer `blast_api`: nur SELECT auf die Metadaten-Tabellen und Views, INSERT nur auf "
        "blast_job, INSERT/UPDATE nur auf blast_api_hits; Zugangsdaten in `/etc/blast_api.ini` (root:www-data, "
        "640). Alle Abfragen mit Prepared Statements, Eingaben per Muster geprüft; `blastdbcmd` wird ohne Shell "
        "mit geprüfter Accession/Range aufgerufen. 60 Anfragen je Minute und Client. Suchen nur mit Schlüssel, "
        "höchstens 3 offene Aufträge je Schlüssel, Tageskontingent je Schlüssel (Standard 20). Die job_id ist "
        "96 bit Zufall und zugleich die Berechtigung zum Lesen des Ergebnisses. Accession-Abfragen antworten mit "
        "503, bis der Index `idx_acc` existiert – ohne ihn wäre jede Abfrage ein Scan über 1,9 Mrd. Zeilen.",
        "Dedicated MariaDB user `blast_api`: SELECT only on the metadata tables and views, INSERT only on "
        "blast_job, INSERT/UPDATE only on blast_api_hits; credentials in `/etc/blast_api.ini` (root:www-data, "
        "640). All queries use prepared statements, inputs are pattern-checked; `blastdbcmd` is called without "
        "a shell and with a validated accession/range. 60 requests per minute and client. Searches only with a "
        "key, at most 3 open jobs per key, daily quota per key (default 20). The job_id is 96 random bits and "
        "at the same time the permission to read the result. Accession requests return 503 until the index "
        "`idx_acc` exists – without it every request would scan 1.9 billion rows.")
    d.p("**Schlüssel verwalten** (auf dem dell): `python3 ~/python/blast_api_key.py add NAME [EMAIL] "
        "[JOBS_PRO_TAG]` gibt den Schlüssel einmalig aus; `list` zeigt alle mit Zahl der Aufträge; `off KEY_ID` "
        "sperrt einen.",
        "**Managing keys** (on the dell): `python3 ~/python/blast_api_key.py add NAME [EMAIL] [JOBS_PRO_TAG]` "
        "prints the key once; `list` shows all keys with their job count; `off KEY_ID` disables one.")

    d.h2("Test (23.09.2026)", "Test (2026-09-23)")
    d.p("Von außen (heissa.de, IPv6): Beschreibung, release, Fehlerfälle (kein/falscher Schlüssel, ungültige "
        "Sequenz, ungültige id, Index fehlt) liefern die erwarteten Codes. Suchweg Ende zu Ende gegen eine "
        "Test-dmnd (Tagesdatei nc0920, 6.491 Sequenzen): blastp der humanen Sequenz BIN93201.1 mit "
        "`taxonlist=9606` → 1 Treffer, 100 % Identität, E-Wert 3e-72, 0,5 s. Titel und Taxon-Namen erscheinen, "
        "sobald der Metadatenimport Index und Taxonomie geladen hat. Testdaten danach entfernt. Der `acc`-Weg "
        "ist noch nicht Ende zu Ende gelaufen: solange `diamond makedb` läuft, antwortet die Suche mit 503.",
        "From outside (heissa.de, IPv6): description, release and the error cases (missing/wrong key, invalid "
        "sequence, invalid id, missing index) return the expected codes. Search path end to end against a test "
        "dmnd (daily file nc0920, 6,491 sequences): blastp of the human sequence BIN93201.1 with "
        "`taxonlist=9606` → 1 hit, 100 % identity, E-value 3e-72, 0.5 s. Titles and taxon names appear once the "
        "metadata import has built the index and loaded the taxonomy. Test data removed afterwards. The `acc` "
        "path has not yet run end to end: while `diamond makedb` is running, searches return 503.")

    d.h2("Beispiel: SARS-CoV-2-Spike-RBD gegen humane Proteine",
         "Example: SARS-CoV-2 spike RBD against human proteins")
    d.p("Frage: hat die Rezeptorbindedomäne (RBD) des Spike-Proteins Ähnlichkeit mit menschlichen Proteinen? "
        "Übergeben wird nur die **Query**, also die Sequenz, zu der Treffer gesucht werden. Die Vergleichsseite "
        "ist immer die komplette nr (`nr_full.dmnd`); `taxonlist=9606` beschränkt die Treffer auf __Homo "
        "sapiens__, `mode=blastp` vergleicht Protein gegen Protein.",
        "Question: does the receptor-binding domain (RBD) of the spike protein resemble human proteins? Only the "
        "**query** is sent, i.e. the sequence for which hits are searched. The subject side is always the "
        "complete nr (`nr_full.dmnd`); `taxonlist=9606` restricts hits to __Homo sapiens__, `mode=blastp` "
        "compares protein against protein.")
    d.p("**Variante A – Query per Accession.** Die Spike-Sequenz liegt selbst in nr (YP_009724390.1, 1.273 aa). "
        "Die RBD sind die Reste 319–541 (223 aa); die API schneidet sie per `range` heraus, die Query heißt dann "
        "`YP_009724390.1_319-541`:",
        "**Option A – query by accession.** The spike sequence itself is in nr (YP_009724390.1, 1,273 aa). The "
        "RBD is residues 319–541 (223 aa); the API cuts it out with `range`, the query is then named "
        "`YP_009724390.1_319-541`:")
    d.code("""
curl -X POST -H 'X-Api-Key: YOUR_API_KEY' -H 'Content-Type: application/json' \\
  -d '{"acc":"YP_009724390.1","range":"319-541","mode":"blastp",
       "taxonlist":"9606","max_target_seqs":25}' \\
  'https://yt.heissa.de/blast/api.php?r=search'
""")
    d.p("**Variante B – Query als Sequenz**, z.B. für eine eigene oder noch nicht in nr enthaltene Variante. "
        "Dieselbe RBD als FASTA; Zeilenumbrüche in der Sequenz sind erlaubt:",
        "**Option B – query as a sequence**, e.g. for your own variant or one not yet in nr. The same RBD as "
        "FASTA; line breaks within the sequence are allowed:")
    rbd = "\n".join(RBD[i:i + 60] for i in range(0, len(RBD), 60))
    d.code(f"""
cat > rbd.fa <<'EOF'
>SARS-CoV-2_Spike_RBD
{rbd}
EOF
curl -X POST -H 'X-Api-Key: YOUR_API_KEY' \\
  --data-urlencode seq@rbd.fa -d mode=blastp -d taxonlist=9606 -d max_target_seqs=25 \\
  'https://yt.heissa.de/blast/api.php?r=search'
""")
    d.p("Als JSON geht es ebenso, dann steht die Sequenz in einer Zeile im Feld `seq` (FASTA-Kopf mit `\\n` "
        "davor). `seq` und `acc` zusammen werden abgelehnt (400); eine Accession, die nicht in der BLAST-nr vom "
        "16.09.2026 steht, ergibt 404 – dann Variante B nehmen.",
        "JSON works as well; the sequence then goes on one line in the `seq` field (FASTA header followed by "
        "`\\n`). `seq` and `acc` together are rejected (400); an accession that is not in the BLAST nr of "
        "2026-09-16 returns 404 – use option B then.")
    d.p("**Antwort und Ergebnis:** der POST liefert sofort 202 mit `job_id`, Position in der Warteschlange und "
        "Ergebnis-URL; diese abfragen, bis `status` = `done` ist:",
        "**Response and result:** the POST returns 202 immediately with `job_id`, queue position and result URL; "
        "poll it until `status` = `done`:")
    d.code("""
{ "job_id": "…", "status": "queued", "position": 1,
  "url": "https://yt.heissa.de/blast/api.php?r=job&id=…" }

curl 'https://yt.heissa.de/blast/api.php?r=job&id=…'
""")
    d.p("Jeder Treffer enthält die DIAMOND-Spalten (`sseqid`, `pident`, `length`, `qstart`–`qend`, "
        "`sstart`–`send`, `evalue`, `bitscore`) plus Titel und Taxon-Namen aus MariaDB. Aussagekräftig sind vor "
        "allem E-Wert und Identität über die ausgerichtete Länge; kurze Treffer mit E-Wert nahe 0,001 sind "
        "meist Zufall.",
        "Each hit contains the DIAMOND columns (`sseqid`, `pident`, `length`, `qstart`–`qend`, `sstart`–`send`, "
        "`evalue`, `bitscore`) plus title and taxon names from MariaDB. E-value and identity over the aligned "
        "length are what matters; short hits with an E-value close to 0.001 are usually chance.")

    # ---------------------------------------------------------------- 8 AmyloDeep
    d.h1("AmyloDeep auf der Tesla P4", "AmyloDeep on the Tesla P4")
    d.p("Zweite Anwendung auf denselben Sequenzen: Vorhersage der Amyloid-Neigung mit Protein-Sprachmodellen. "
        "AmyloDeep 0.3.1 (Davtyan, Khachatryan & Petrosyan, bioRxiv 2025, doi 10.1101/2025.09.16.676495; PyPI "
        "`amylodeep`, Gewichte `AlisaDavtyan/amylodeep-models` auf Hugging Face) ist ein Ensemble aus fünf "
        "Modellen: ESM-2 150M (feinjustiert), UniRep mit Klassifikationskopf, ESM-2 650M (Mean-Embedding) mit "
        "Kopf sowie SVM und XGBoost auf denselben 650M-Embeddings. Die Einzelwahrscheinlichkeiten werden "
        "kalibriert (Platt bzw. isotonisch) und gemittelt. Bewertet wird ein Rollfenster (Standard 10 Reste, "
        "Schritt 1); jedes Fenster wird auf 128 Token aufgefüllt.",
        "A second application on the same sequences: predicting amyloid propensity with protein language "
        "models. AmyloDeep 0.3.1 (Davtyan, Khachatryan & Petrosyan, bioRxiv 2025, doi 10.1101/2025.09.16.676495; "
        "PyPI `amylodeep`, weights `AlisaDavtyan/amylodeep-models` on Hugging Face) is an ensemble of five "
        "models: ESM-2 150M (fine-tuned), UniRep with a classification head, ESM-2 650M (mean embedding) with a "
        "head, and SVM and XGBoost on the same 650M embeddings. The individual probabilities are calibrated "
        "(Platt or isotonic) and averaged. Scoring uses a rolling window (default 10 residues, step 1); each "
        "window is padded to 128 tokens.")

    d.h2("Passt es in die P4?", "Does it fit on the P4?")
    d.tabelle([("Modell", "Model"), ("Gewichte FP32", "Weights FP32"), ("auf der P4", "on the P4")], [
        [("ESM-2 150M (feinjustiert)", "ESM-2 150M (fine-tuned)"), "~0.6 GB", ("ja", "yes")],
        ["ESM-2 650M (esm2_t33_650M_UR50D)", "~2.6 GB", ("ja", "yes")],
        [("zusammen, dazu llama-server (0,8 GB)", "together, plus llama-server (0.8 GB)"),
         ("~4,1 GB von 7,5 GB", "~4.1 GB of 7.5 GB"), ("ja, FP32, kein FP16 nötig", "yes, FP32, no FP16 needed")],
        [("zum Vergleich: ESM-2 3B", "for comparison: ESM-2 3B"),
         ("~11,4 GB (FP16 ~5,7 GB)", "~11.4 GB (FP16 ~5.7 GB)"),
         ("nein bzw. nur ohne llama-server", "no, or only without llama-server")],
    ], [6.5, 4.5, 6.0])
    d.p("UniRep läuft über JAX auf der CPU, SVM und XGBoost ebenfalls; die Torch-Modelle liegen auf der GPU.",
        "UniRep runs via JAX on the CPU, as do SVM and XGBoost; the Torch models live on the GPU.")

    d.h2("Umgebung: Env `iver` mit CUDA 11.8", "Environment: env `iver` with CUDA 11.8")
    d.p("Das micromamba-Env `iver` (`~/iver_sim/mamba/envs/iver`) war auf CUDA 11.8 gepinnt, enthielt aber nur "
        "einen **CPU-Build** von PyTorch 2.5.1. Ersetzt durch den conda-forge-Build `pytorch 2.5.1 cuda118`; "
        "dessen Architekturliste enthält `sm_61` nativ, ein eigener Compile war nicht nötig. Dazu kamen cudnn, "
        "magma und nccl; libprotobuf 5.28.3 → 5.28.2.",
        "The micromamba env `iver` (`~/iver_sim/mamba/envs/iver`) was pinned to CUDA 11.8 but only contained a "
        "**CPU build** of PyTorch 2.5.1. It was replaced by the conda-forge build `pytorch 2.5.1 cuda118`, whose "
        "architecture list includes `sm_61` natively, so no custom compile was needed. cudnn, magma and nccl "
        "came along; libprotobuf 5.28.3 → 5.28.2.")
    d.p("Der nächste Install hätte PyTorch sofort wieder auf 2.10 CPU und numpy auf 2.x gezogen. Deshalb stehen in "
        "`conda-meta/pinned` jetzt zusätzlich `pytorch 2.5.1 cuda118*`, `libtorch 2.5.1 cuda118*` und "
        "`numpy 1.26.*` (Sicherung `pinned.bak-20260923`). Installiert: transformers 5.16.1, huggingface_hub "
        "1.32, jax/jaxlib 0.5.2 (CPU), xgboost 3.4.2 (CPU), equinox, optax, optuna; `amylodeep 0.3.1` und "
        "`jax-unirep 3.0.0` per pip mit `--no-deps`. `openmm.testInstallation` danach unverändert fehlerfrei "
        "(CUDA und OpenCL).",
        "The next install would immediately have pulled PyTorch back to 2.10 CPU and numpy to 2.x. "
        "`conda-meta/pinned` therefore now also contains `pytorch 2.5.1 cuda118*`, `libtorch 2.5.1 cuda118*` and "
        "`numpy 1.26.*` (backup `pinned.bak-20260923`). Installed: transformers 5.16.1, huggingface_hub 1.32, "
        "jax/jaxlib 0.5.2 (CPU), xgboost 3.4.2 (CPU), equinox, optax, optuna; `amylodeep 0.3.1` and "
        "`jax-unirep 3.0.0` via pip with `--no-deps`. `openmm.testInstallation` still passes (CUDA and OpenCL).")
    d.p("**Ausführbarer Stack:** Kernel 7.0 mit glibc 2.43 lädt keine Bibliotheken mehr, die einen ausführbaren "
        "Stack anfordern (`cannot enable executable stack`). Betroffen war nur `jaxlib/xla_extension.so`: im "
        "Programmkopf `PT_GNU_STACK` das X-Bit gelöscht (RWE → RW), Original unter "
        "`~/iver_sim/xla_extension.so.orig`. Nach einem jaxlib-Update ist das zu wiederholen.",
        "**Executable stack:** kernel 7.0 with glibc 2.43 no longer loads libraries that request an executable "
        "stack (`cannot enable executable stack`). Only `jaxlib/xla_extension.so` was affected: the X bit in "
        "its `PT_GNU_STACK` program header was cleared (RWE → RW); the original is kept as "
        "`~/iver_sim/xla_extension.so.orig`. This has to be repeated after a jaxlib update.")

    d.h2("`amylo_gpu.py`", "`amylo_gpu.py`")
    d.p("`~/iver_sim/ol_amyloid/amylo_gpu.py` erbt von `EnsembleRollingWindowPredictor`, ohne das Paket zu "
        "verändern. Das Paket selbst rechnet nur auf der CPU und führt ESM-2 650M für dieselben Fenster dreimal "
        "aus (NN-Kopf, SVM, XGBoost). Der Wrapper legt die Torch-Modelle auf CUDA, rechnet in Stapeln zu 64 und "
        "die 650M-Embeddings je Fenstermenge nur einmal. Eingabe: Sequenz, FASTA, `--acc` oder `--kandidaten` "
        "(TSV Accession, Kürzel); Sequenzen per `blastdbcmd` aus der lokalen nr (siehe Abschnitt 9).",
        "`~/iver_sim/ol_amyloid/amylo_gpu.py` subclasses `EnsembleRollingWindowPredictor` without modifying the "
        "package. The package itself only runs on the CPU and evaluates ESM-2 650M three times for the same "
        "windows (NN head, SVM, XGBoost). The wrapper moves the Torch models to CUDA, works in batches of 64 and "
        "computes the 650M embeddings only once per set of windows. Input: sequence, FASTA, `--acc` or "
        "`--kandidaten` (TSV accession, short name); sequences come from the local nr via `blastdbcmd` (see "
        "section 9).")
    d.p("**Prüfung gegen das Original** (`--vergleich`), Aβ42 (42 aa, 33 Fenster): größte Abweichung je Fenster "
        "7e-7, GPU 4,4 s gegen 43,4 s CPU. Die stärksten Fenster liegen auf den bekannten Aggregationskernen: "
        "`GLMVGGVVIA` 0,91 (C-Terminus), `KLVFFAEDVG` 0,90 (KLVFF, Reste 16–20); am schwächsten der N-Terminus "
        "`DAEFRHDSGY` 0,07.",
        "**Check against the original** (`--vergleich`), Aβ42 (42 aa, 33 windows): largest difference per "
        "window 7e-7, GPU 4.4 s vs. 43.4 s CPU. The strongest windows lie on the known aggregation cores: "
        "`GLMVGGVVIA` 0.91 (C terminus), `KLVFFAEDVG` 0.90 (KLVFF, residues 16–20); the weakest is the N "
        "terminus `DAEFRHDSGY` 0.07.")
    d.code("""
cd ~/iver_sim/ol_amyloid
PY=~/iver_sim/mamba/envs/iver/bin/python
$PY amylo_gpu.py --import-tsv kontrolle.tsv --rolle kontrolle    # Kandidaten anlegen (acc, kurz, name)
$PY amylo_gpu.py --kandidaten amyloid --bemerkung "43 amyloid candidates"
$PY amylo_gpu.py --acc YP_009724390.1 -w 10
""")

    d.h2("Tabellen in wagodb (Präfix `amyl_`)", "Tables in wagodb (prefix `amyl_`)")
    d.p("Schema `~/iver_sim/ol_amyloid/amyl_schema.sql`. Jede Angabe steht genau einmal: Kandidat → "
        "Sequenzversion → Ergebnis je Lauf → Fenster. Fenstertexte, Fensterzahl und NCBI-Titel/Taxon werden "
        "nicht gespeichert, sondern in den Views abgeleitet. Die Kandidatenliste lebt in `amyl_kandidat`, nicht "
        "mehr in einer TSV-Datei.",
        "Schema `~/iver_sim/ol_amyloid/amyl_schema.sql`. Every fact is stored exactly once: candidate → "
        "sequence version → result per run → window. Window texts, window count and NCBI title/taxon are not "
        "stored but derived in the views. The candidate list lives in `amyl_kandidat`, no longer in a TSV "
        "file.")
    d.tabelle([("Tabelle", "Table"), ("Inhalt", "Content")], [
        ["amyl_kandidat", ("je Protein einmal: Kürzel, Name, Accession ohne Version, Rolle (amyloid / kontrolle / "
                           "einzel), reife Kette von–bis (NULL = ganze Sequenz), Bemerkung",
                           "once per protein: short name, name, accession without version, role (amyloid / "
                           "kontrolle / einzel), mature chain from–to (NULL = whole sequence), remark")],
        ["amyl_sequenz", ("je Sequenzversion einmal: Kandidat, versionierte Accession (wie blast_acc), Quelle, "
                          "Länge, Sequenz, SHA1",
                          "once per sequence version: candidate, versioned accession (as in blast_acc), source, "
                          "length, sequence, SHA1")],
        ["amyl_lauf", ("je Aufruf: Start, Laufzeit, Host, GPU, Software-Stände, Fenstergröße, Zahl der Sequenzen, "
                       "Bemerkung",
                       "per call: start, run time, host, GPU, software versions, window size, number of "
                       "sequences, remark")],
        ["amyl_ergebnis", ("je Lauf und Sequenz: bewerteter Bereich, Mittel, Maximum, Position des Maximums, "
                           "Laufzeit",
                           "per run and sequence: scored range, mean, maximum, position of the maximum, run "
                           "time")],
        ["amyl_fenster", ("je Fenster: Position (1-basiert, auf die ganze Sequenz bezogen), Wahrscheinlichkeit",
                          "per window: position (1-based, relative to the whole sequence), probability")],
        [("amyl_v_ergebnis, amyl_v_fenster (Views)", "amyl_v_ergebnis, amyl_v_fenster (views)"),
         ("Ergebnisse mit Kandidat, stärkstem Fenster (`SUBSTRING`), Fensterzahl sowie Titel und Taxon aus "
          "blast_acc/blast_seq/blast_taxon",
          "results with candidate, strongest window (`SUBSTRING`), window count, plus title and taxon from "
          "blast_acc/blast_seq/blast_taxon")],
    ], [3.8, 13.2])
    d.bild("amyl", "ER-Diagramm AmyloDeep (grau: blast_acc, über die versionierte Accession), erzeugt aus dem Live-Schema (information_schema). Durchgezogen: deklarierte Fremdschlüssel, gestrichelt: logische Verknüpfungen; Krähenfuß = viele. PK/UK/IX/FK wie in der Datenbank.",
           "ER diagram AmyloDeep (grey: blast_acc, via the versioned accession), generated from the live schema (information_schema). Solid: declared foreign keys, dashed: logical links; crow's foot = many. PK/UK/IX/FK as in the database.")
    d.p("Umstellung am 23.09.2026 (`amyl_migration.py`, Sicherung `amyl_alt_20260923.sql`): 43 Kandidaten, 43 "
        "Ergebnisse, 16.739 Fenster übernommen, abgeleitete Fenstertexte, stärkste Fenster und Fensterzahlen ohne "
        "eine Abweichung zum alten Bestand. Schreibweg pymysql über `iv_db.cfg()`, jede Sequenz für sich "
        "festgeschrieben.",
        "Migrated on 2026-09-23 (`amyl_migration.py`, backup `amyl_alt_20260923.sql`): 43 candidates, 43 results, "
        "16,739 windows; derived window texts, strongest windows and window counts match the old data without a "
        "single difference. Written with pymysql via `iv_db.cfg()`, each sequence committed on its own.")

    d.h2("Ergebnis: 43 Amyloid-Kandidaten", "Result: 43 amyloid candidates")
    if not amyl:
        d.p("Noch kein vollständiger Lauf in `amyl_ergebnis`.", "No complete run in `amyl_ergebnis` yet.")
    else:
        lid, start, sek, gpu, w, n, _ = amyl["lauf"]
        d.p(f"Lauf `amyl_lauf.lauf_id = {lid}` ({start:%d.%m.%Y %H:%M}, {gpu}, Fenster {w}): {n} Proteine aus "
            f"`amyl_kandidat`, {amyl['n_fenster']:,} Fenster, {sek / 60:.0f} min.".replace(",", "."),
            f"Run `amyl_lauf.lauf_id = {lid}` ({start:%Y-%m-%d %H:%M}, {gpu}, window {w}): {n} proteins from "
            f"`amyl_kandidat`, {amyl['n_fenster']:,} windows, {sek / 60:.0f} min.")
        AMYL_TEXT(d, amyl)
        zahl = (lambda x: f"{x:.2f}".replace(".", ",")) if d.lang == "de" else (lambda x: f"{x:.2f}")
        d.tabelle([("Kürzel", "Short"), "Accession", ("Länge", "Length"), ("Mittel", "Mean"), "Max",
                   ("Pos.", "Pos."), ("stärkstes Fenster", "strongest window")],
                  [[k or "", a or "", str(l), zahl(av), zahl(mx), str(mp), f"`{mf}`"]
                   for k, a, l, av, mx, mp, mf, _ in amyl["seqs"]],
                  [1.7, 2.2, 1.3, 1.3, 1.2, 1.2, 3.0])

    d.h2("API und Worker (`amylo-api-worker`)", "API and worker (`amylo-api-worker`)")
    d.p("AmyloDeep hängt nicht mehr an einer SSH-Sitzung: dieselbe JSON-API wie für DIAMOND nimmt Aufträge "
        "entgegen, mit denselben Schlüsseln, derselben Ratenbegrenzung und demselben Warteschlangen-Muster. "
        "Der Grund für einen eigenen Worker ist das Modell: es einmal zu laden kostet 6,5 s, und ein Aufruf von "
        "`amylo_gpu.py` zahlt das jedes Mal. Der Worker (systemd `amylo-api-worker`, Env `iver`) hält ESM-2 "
        "150M/650M, UniRep, SVM und XGBoost dauerhaft auf der Tesla P4 (4,6 GB) und arbeitet die Aufträge "
        "nacheinander ab.",
        "AmyloDeep no longer depends on an SSH session: the same JSON API as for DIAMOND accepts jobs, with the "
        "same keys, the same rate limit and the same queue pattern. The reason for a dedicated worker is the "
        "model: loading it once costs 6.5 s, and every call to `amylo_gpu.py` pays that again. The worker "
        "(systemd `amylo-api-worker`, env `iver`) keeps ESM-2 150M/650M, UniRep, SVM and XGBoost on the Tesla P4 "
        "(4.6 GB) and processes jobs one at a time.")
    d.tabelle([("Aufruf", "Call"), ("Zugang", "Access"), ("Liefert", "Returns")], [
        ["POST ?r=amylo", "X-Api-Key",
         ("legt einen Auftrag an: `acc` (aus nr, optional `von`/`bis`) **oder** `seq`, `window` 4–40 "
          "(Standard 10); Antwort 202 mit job_id und Ergebnis-URL",
          "creates a job: `acc` (from nr, optional `von`/`bis`) **or** `seq`, `window` 4–40 (default 10); "
          "response 202 with job_id and result URL")],
        ["GET ?r=amylojob&id=…", ("wer die id kennt", "anyone with the id"),
         ("Status, Parameter, Laufzeit; wenn fertig Mittel, Maximum, stärkstes Fenster und alle Fensterwerte",
          "status, parameters, run time; when done the mean, maximum, strongest window and every window value")],
    ], [4.0, 2.6, 10.4])
    d.p("Grenzen wie bei der Suche: höchstens 3 offene Aufträge und ein Tageskontingent je Schlüssel, 60 "
        "Anfragen je Minute, bis 5.000 Reste je Auftrag. Der Benutzer `blast_api` darf `amyl_job` schreiben und "
        "die Ergebnistabellen nur lesen. Jeder Auftrag erzeugt einen eigenen `amyl_lauf`, die Werte landen in "
        "denselben Tabellen wie die Läufe von Hand (8.4) – ein API-Ergebnis ist damit später genauso auswertbar.",
        "Limits as for the search: at most 3 open jobs and a daily quota per key, 60 requests per minute, up to "
        "5,000 residues per job. The user `blast_api` may write `amyl_job` and only read the result tables. Every "
        "job creates its own `amyl_lauf`, and the values go into the same tables as manual runs (8.4) – so an "
        "API result stays just as analysable later.")

    d.h2("Beispiel: eine Literaturaussage prüfen (Spike 194–203)",
         "Example: checking a published claim (spike 194–203)")
    d.p("Der Fall zeigt, was die Teile zusammen leisten. Ausgangspunkt ist eine Angabe aus der Literatur "
        "(Nyström & Hammarström, __JACS__ 2022, hier als Vorgabe übernommen und **nicht** nachgeprüft): im "
        "SARS-CoV-2-Spike gebe es sieben amyloidogene Abschnitte, und das Fragment **194–203** bilde nach "
        "Spaltung durch neutrophile Elastase Fibrillen. Geprüft wird nur, ob AmyloDeep diesen Abschnitt "
        "auffällig findet; Spaltstellen sagt das Modell nicht vorher.",
        "This case shows what the parts achieve together. The starting point is a claim from the literature "
        "(Nyström & Hammarström, __JACS__ 2022, taken here as given and **not** verified): the SARS-CoV-2 spike "
        "contains seven amyloidogenic segments, and fragment **194–203** forms fibrils after cleavage by "
        "neutrophil elastase. What is tested is only whether AmyloDeep finds this segment conspicuous; the "
        "model does not predict cleavage sites.")
    d.p("Aus 8.5 folgt die Methodik: ein hoher Wert allein sagt nichts, weil alle 43 bekannten Amyloidbildner "
        "zwischen 0,88 und 0,97 lagen. Es braucht zwei Bezugsgrößen: **intern** den Rang des Fensters 194 unter "
        "allen Fenstern des Spike, **extern** die Verteilung der Fenster eines Kontrollsatzes zufälliger humaner "
        "SwissProt-Proteine (100–800 aa, bekannte Amyloidbildner ausgeschlossen, feste Zufallszahl).",
        "Section 8.5 dictates the method: a high value alone says nothing, since all 43 known amyloid formers "
        "scored between 0.88 and 0.97. Two reference points are needed: **internal**, the rank of window 194 "
        "among all windows of the spike, and **external**, the distribution of windows from a control set of "
        "random human SwissProt proteins (100–800 aa, known amyloid formers excluded, fixed random seed).")
    d.p("`~/iver_sim/ol_amyloid/spike_amyloid_test.py` legt Kandidaten und Kontrollen an und wertet danach aus; "
        "gerechnet wird über die API (8.5), der Worker hält das Modell bereit:",
        "`~/iver_sim/ol_amyloid/spike_amyloid_test.py` creates candidates and controls and evaluates afterwards; "
        "the computing goes through the API (8.5), with the worker holding the model ready:")
    d.code("""
# Kandidat und 300 SwissProt-Kontrollen anlegen
python3 spike_amyloid_test.py --vorbereiten --n-kontrollen 300 --seed 42

# Auftrag ueber die API (Abschnitt 8.5) - kein SSH, kein Modell-Laden
curl -X POST -H "X-Api-Key: $K" -H 'Content-Type: application/json' \\
  -d '{"acc":"YP_009724390.1","window":10}' \\
  'https://yt.heissa.de/blast/api.php?r=amylo'
  -> {"job_id":"0f478cae...","status":"queued","position":1,"url":"...r=amylojob&id=..."}

curl 'https://yt.heissa.de/blast/api.php?r=amylojob&id=0f478cae...'
python3 spike_amyloid_test.py --auswerten
""")
    d.p("**Was dabei ineinandergreift** – und warum das ohne den Unterbau der vorigen Abschnitte Tage statt "
        "Minuten dauern würde:",
        "**What comes together here** – and why this would take days instead of minutes without the "
        "infrastructure of the previous sections:")
    d.tabelle([("Schritt", "Step"), ("Grundlage", "Basis"), ("Aufwand", "Cost")], [
        [("Spike-Sequenz und 300 Kontrollsequenzen holen", "fetch the spike sequence and 300 control sequences"),
         ("lokale nr per Accession (Abschnitt 4, 9)", "local nr by accession (sections 4, 9)"),
         ("~0,3 s je Sequenz, kein Download", "~0.3 s per sequence, no download")],
        [("Kontrollen auswählen", "select controls"),
         ("`swissprot_titel.tsv` lokal, Filter nach Organismus, Länge und Titel",
          "`swissprot_titel.tsv` locally, filtered by organism, length and title"),
         ("15.603 Kandidaten, Auswahl per Zufallszahl reproduzierbar",
          "15,603 candidates, selection reproducible via the seed")],
        [("Amyloid-Neigung je Fenster", "amyloid propensity per window"),
         ("ESM-2 650M + Ensemble auf der Tesla P4 (8.1–8.3), eingereicht über die API (8.5)",
          "ESM-2 650M + ensemble on the Tesla P4 (8.1–8.3), submitted through the API (8.5)"),
         ("1.264 Fenster in 136 s; GPU 100 %, 78 °C", "1,264 windows in 136 s; GPU at 100 %, 78 °C")],
        [("Ergebnisse ablegen", "store results"),
         ("`amyl_*` in wagodb, je Angabe einmal (8.4)", "`amyl_*` in wagodb, each fact once (8.4)"),
         ("Auswertung als SQL statt als Dateiwust", "evaluation in SQL instead of a pile of files")],
        [("Nachvollziehbarkeit", "reproducibility"),
         ("`amyl_lauf` hält Zeit, GPU, Software-Stände, Fenstergröße fest",
          "`amyl_lauf` records time, GPU, software versions, window size"),
         ("jeder Wert im PDF ist auf einen Lauf zurückführbar",
          "every figure in this PDF traces back to one run")],
    ], [5.4, 6.3, 5.3])

    if not spike:
        d.p("Der Lauf steht noch aus; die Zahlen erscheinen, sobald `amylo_gpu.py --acc YP_009724390.1` "
            "gerechnet hat.",
            "The run is still pending; the figures appear once `amylo_gpu.py --acc YP_009724390.1` has run.")
    else:
        sp = spike
        k = (lambda x, n=3: f"{x:.{n}f}".replace(".", ",")) if d.lang == "de" else (lambda x, n=3: f"{x:.{n}f}")
        t = (lambda x: f"{x:,}".replace(",", ".")) if d.lang == "de" else (lambda x: f"{x:,}")
        pz = 100 * (sp["n_fen"] - sp["rang"]) / sp["n_fen"]
        d.p(f"**Ergebnis** (Lauf {sp['lauf']}, {sp['laenge']} aa, Fenster {sp['w']}): das Fenster ab Position 194 "
            f"(`FKNIDGYFKI`) erreicht {k(sp['prob'])} und liegt damit auf Rang {sp['rang']} von "
            f"{t(sp['n_fen'])} Fenstern des Spike-Proteins, also über {k(pz, 0)} % der übrigen Abschnitte.",
            f"**Result** (run {sp['lauf']}, {sp['laenge']} aa, window {sp['w']}): the window starting at position "
            f"194 (`FKNIDGYFKI`) scores {k(sp['prob'])}, ranking {sp['rang']} of {t(sp['n_fen'])} spike windows, "
            f"above {k(pz, 0)} % of the remaining segments.")
        d.tabelle([("Rang", "Rank"), ("Position", "Position"), ("Fenster", "Window"), ("Wert", "Score")],
                  [[str(i + 1), str(p), f"`{fen}`", k(pr)] for i, (p, fen, pr) in enumerate(sp["top"])],
                  [1.5, 2.0, 3.5, 2.0])
        d.p("**Die Vorhersage stützt die Angabe nicht.** Das Fragment liegt im Mittelfeld des eigenen Proteins, "
            "nicht an der Spitze. Die höchsten Werte stehen auf hydrophoben Abschnitten der Transmembran- und "
            "Fusionsregion (1218, 1222, 1059) – dasselbe Muster wie bei den 43 Kandidaten, wo 19 Maxima im "
            "Signalpeptid lagen. Mögliche Gründe, keiner davon hier geprüft: AmyloDeep bewertet ein Fenster für "
            "sich, nicht das Verhalten eines durch Elastase freigesetzten Peptids; die zitierte Arbeit ist "
            "experimentell, nicht vorhersagend; und andere Verfahren (WALTZ, AGGRESCAN, ZipperDB) können anders "
            "urteilen. Belegt ist nur: **dieses** Modell findet **diesen** Abschnitt unauffällig.",
            "**The prediction does not support the claim.** The fragment sits mid-field within its own protein, "
            "not at the top. The highest scores fall on hydrophobic stretches of the transmembrane and fusion "
            "region (1218, 1222, 1059) – the same pattern as with the 43 candidates, where 19 maxima lay in the "
            "signal peptide. Possible reasons, none of them tested here: AmyloDeep scores a window in isolation, "
            "not the behaviour of a peptide released by elastase; the cited work is experimental, not "
            "predictive; and other methods (WALTZ, AGGRESCAN, ZipperDB) may judge differently. What is "
            "established is only this: **this** model finds **this** segment unremarkable.")
        if not sp["n_kfen"]:
            d.p("Der SwissProt-Kontrollsatz (300 Proteine) ist angelegt, aber noch nicht gerechnet; er würde "
                "zeigen, wie ein Wert von 0,63 im Vergleich zu beliebigen humanen Abschnitten einzuordnen ist.",
                "The SwissProt control set (300 proteins) has been created but not yet computed; it would show "
                "how a score of 0.63 compares to arbitrary human segments.")
        else:
            anteil = 100 * sp["n_hoeher"] / sp["n_kfen"]
            d.p(f"**Gegen die Kontrollen:** {sp['n_prot']} humane SwissProt-Proteine ergeben "
                f"{t(sp['n_kfen'])} Fenster; {k(anteil, 2)} % davon erreichen {k(sp['prob'])} oder mehr. "
                f"Je kleiner dieser Anteil, desto auffälliger ist das Fragment; liegt er im Prozentbereich, "
                f"trennt AmyloDeep den Abschnitt nicht vom Hintergrund.",
                f"**Against the controls:** {sp['n_prot']} human SwissProt proteins yield {t(sp['n_kfen'])} "
                f"windows; {k(anteil, 2)} % of them reach {k(sp['prob'])} or more. The smaller this share, the "
                f"more conspicuous the fragment; if it lies in the percent range, AmyloDeep does not separate "
                f"the segment from the background.")

    # ---------------------------------------------------------------- 9 Doppelte Datenhaltung
    d.h1("Doppelte Datenhaltung auflösen", "Removing the duplicate storage")
    d.p("Dieselben Sequenzen liegen zweimal auf dem dell: als BLAST-nr und als DIAMOND-Datenbank.",
        "The same sequences are stored twice on the dell: as BLAST nr and as DIAMOND database.")
    d.tabelle([("Datenbestand", "Data set"), ("Größe", "Size"), ("Ort", "Location"), ("Stand", "State")], [
        [("`~/blastdb/nr` (BLAST, 176 Volumes)", "`~/blastdb/nr` (BLAST, 176 volumes)"), "857 GB",
         ("System-NVMe (78 % belegt)", "system NVMe (78 % used)"), ("eingefroren 16.09.2026", "frozen 2026-09-16")],
        [("`nr_full.dmnd` (DIAMOND)", "`nr_full.dmnd` (DIAMOND)"),
         ("Neubau läuft (ohne Titel, erwartet ~460 GB)", "rebuild running (without titles, expected ~460 GB)"),
         "`/mnt/archive` (sda5)",
         ("per Cron fortgeschrieben (5.2, 5.3)", "updated by cron (5.2, 5.3)")],
        [("Metadaten `blast_*`", "Metadata `blast_*`"),
         ("2 × ~52 GB + Indizes (hochgerechnet, 6.1)", "2 × ~52 GB + indexes (extrapolated, 6.1)"),
         "MariaDB (NVMe)", ("per Cron fortgeschrieben (6.4)", "updated by cron (6.4)")],
    ], [5.2, 3.6, 3.6, 4.6])
    d.p("Suche, Append und Tagesdelta brauchen die BLAST-nr nicht mehr (die Hash-Arrays für den Duplikatabgleich "
        "sind gebaut). Übrig ist ein einziger Zweck: **Residuen zu einer Accession holen** (API `acc`, "
        "`amylo_gpu.py`). Die Metadaten in MariaDB helfen dabei nicht, `blast_seq` enthält Länge und Titel, aber "
        "keine Sequenz. Dazu kommt ein Fehler im Ist-Zustand: die Cron-Skripte hängen neue Sequenzen nur an "
        ".dmnd und MariaDB an, nicht an die BLAST-nr. Eine per Tagesdelta hinzugekommene Accession steht in "
        "`blast_acc`, `blastdbcmd` findet sie aber nicht.",
        "Search, append and daily delta no longer need the BLAST nr (the hash arrays for duplicate detection "
        "are built). One purpose remains: **fetching the residues for an accession** (API `acc`, "
        "`amylo_gpu.py`). The metadata in MariaDB do not help here: `blast_seq` holds length and title but no "
        "sequence. On top of that the current state has a flaw: the cron scripts append new sequences only to "
        "the .dmnd and MariaDB, not to the BLAST nr. An accession added by the daily delta is in `blast_acc`, "
        "but `blastdbcmd` cannot find it.")

    d.h2("Titel liegen dreifach vor", "Titles are stored three times")
    d.p("Die BLAST-nr besteht nur zu gut der Hälfte aus Sequenzen: `.psq` 436 GB (1 Byte je Rest), `.phr` 270 GB "
        "Kopfzeilen, ~155 GB Accession- und Taxon-Indizes (`nr.pdb` als LMDB allein 99 GB), ~45 GB OID-Indizes. "
        "Grund: nr fasst identische Sequenzen zusammen, behält aber jede Defline – 1,15 Mrd. Sequenzen, 1,94 Mrd. "
        "Deflines; ein Titel ist im Mittel ~139 Byte, eine Sequenz 377 Reste.",
        "The BLAST nr is only just over half sequence: `.psq` 436 GB (1 byte per residue), `.phr` 270 GB "
        "headers, ~155 GB accession and taxon indexes (`nr.pdb`, an LMDB, alone is 99 GB), ~45 GB OID indexes. "
        "Reason: nr merges identical sequences but keeps every defline – 1.15 billion sequences, 1.94 billion "
        "deflines; an average title is ~139 bytes, an average sequence 377 residues.")
    d.p("Die .dmnd enthält dieselben Titel noch einmal. `diamond_pipeline.sh` ruft `blastdbcmd -entry all` ohne "
        "`-outfmt` auf; das liefert FASTA mit der zusammengefassten Kopfzeile aller identischen Einträge, getrennt "
        "durch „ >“ (Spike YP_009724390.1: 29 Deflines, 2.634 Byte Titel zu 1.273 Resten). `FastaFile` übernimmt "
        "die ganze Zeile als ID (`src/data/fasta/fasta_file.cpp:231`), `push_seq` schreibt sie vollständig hinter "
        "die Residuen (`0xFF`, Residuen, `0xFF`, ID, Nullbyte; `src/legacy/dmnd/dmnd.cpp`). Gebraucht werden davon "
        "nur die Accessions: `accession_from_title` zerlegt die Zeile an `\\1` bzw. „ >“ "
        "(`FASTA_HEADER_SEP`, `src/util/sequence/sequence.cpp:38`) und füllt damit die Taxon-Listen. Grob 120 GB "
        "der .dmnd sind Titeltext (genaue Zahl nach Abschluss des Builds aus dem Trailer). Die dritte Kopie ist "
        "`blast_seq.title` in MariaDB (nur der erste Titel je Sequenz).",
        "The .dmnd contains the same titles once more. `diamond_pipeline.sh` calls `blastdbcmd -entry all` "
        "without `-outfmt`, which emits FASTA with the merged header of all identical entries, separated by “ >” "
        "(spike YP_009724390.1: 29 deflines, 2,634 bytes of titles for 1,273 residues). `FastaFile` takes the "
        "whole line as the ID (`src/data/fasta/fasta_file.cpp:231`), `push_seq` writes it in full after the "
        "residues (`0xFF`, residues, `0xFF`, ID, NUL; `src/legacy/dmnd/dmnd.cpp`). Only the accessions are "
        "needed: `accession_from_title` splits the line at `\\1` or “ >” (`FASTA_HEADER_SEP`, "
        "`src/util/sequence/sequence.cpp:38`) to fill the taxon lists. Roughly 120 GB of the .dmnd are title "
        "text (exact figure from the trailer once the build is done). The third copy is `blast_seq.title` in "
        "MariaDB (first title per sequence only).")
    d.p("**Umgesetzt im Neubau vom 23.09.2026 (`nr_build.py`, Abschnitt 4):** Kopfzeile nur noch aus den "
        "Accessions (`>acc1 >acc2 …`). Taxon-Listen bleiben vollständig, die Suchausgabe `sseqid` ist ohnehin nur "
        "das erste Wort, Titel kommen für die API aus MariaDB. Die .dmnd wird dadurch rund 100 GB kleiner; die "
        "Titel liegen danach nur noch zweimal vor (`.phr` und `blast_seq.title`), nach dem Löschen der BLAST-nr "
        "einmal.",
        "**Implemented in the rebuild of 2026-09-23 (`nr_build.py`, section 4):** headers consist of the "
        "accessions only (`>acc1 >acc2 …`). Taxon lists stay complete, the search output `sseqid` is only the "
        "first word anyway, and the API takes titles from MariaDB. This makes the .dmnd about 100 GB smaller; "
        "titles are then stored twice (`.phr` and `blast_seq.title`), once after the BLAST nr is deleted.")

    d.h2("Warum nicht `diamond getseq`", "Why not `diamond getseq`")
    d.p("`diamond getseq` (`SequenceFile::get_seq()`, `src/data/sequence_file.cpp:395`) hat keinen Index: die "
        "Schleife liest alle `sequence_count()` Sätze der Reihe nach und gibt die ausgewählten aus. `--seq` "
        "erwartet Satznummern, nicht Accessions; Titel-Listen werden ebenfalls nur während dieses Durchlaufs "
        "verglichen. Für eine einzelne Accession hieße das 495 GB lesen – Stunden statt Millisekunden.",
        "`diamond getseq` (`SequenceFile::get_seq()`, `src/data/sequence_file.cpp:395`) has no index: its loop "
        "reads all `sequence_count()` records in order and prints the selected ones. `--seq` expects record "
        "numbers, not accessions; title lists are also only compared during this pass. For a single accession "
        "that means reading 495 GB – hours instead of milliseconds.")

    d.h2("Direktzugriff: `dmnd_getseq.py`", "Direct access: `dmnd_getseq.py`")
    d.p("Die .dmnd hat alles, was für einen Direktzugriff nötig ist: im Trailer liegt ab `pos_array_offset` "
        "(Header) die Offset-Tabelle, 16 Byte je Satz (`SeqInfo`: `pos` u64, `seq_len` u32, "
        "`src/data/sequence_file.h:130`). Geplantes Skript, reines Python, ohne DIAMOND-Aufruf:",
        "The .dmnd has everything needed for direct access: from `pos_array_offset` (header) the trailer holds "
        "the offset table, 16 bytes per record (`SeqInfo`: `pos` u64, `seq_len` u32, "
        "`src/data/sequence_file.h:130`). Planned script, plain Python, no DIAMOND call:")
    d.liste([
        ("Accession → OID über `blast_acc` (Index `idx_acc`). Deckt Erstimport, Release-Append und Tagesdelta ab.",
         "Accession → OID via `blast_acc` (index `idx_acc`). Covers initial import, release append and daily "
         "delta."),
        ("OID → Satznummer der .dmnd: OID minus Zahl der Leersequenzen mit kleinerer OID. Die Leersequenzen "
         "schreibt `nr_build.py` beim Bau in `blast_dmnd_luecke`; angehängte Sequenzen erzeugen keine neuen Lücken, "
         "`--append` lehnt Länge 0 ab (5.1).",
         "OID → record number in the .dmnd: OID minus the number of empty sequences with a smaller OID. "
         "`nr_build.py` records the empty sequences in `blast_dmnd_luecke` during the build; appended sequences "
         "create no new gaps, `--append` rejects length 0 (5.1)."),
        ("Satznummer → Offset: ein `seek` auf `pos_array_offset + 16·n`, 16 Byte lesen.",
         "Record number → offset: one `seek` to `pos_array_offset + 16·n`, read 16 bytes."),
        ("Offset → Residuen: ein `seek`, `seq_len` Bytes lesen, DIAMOND-Buchstabencode in Aminosäuren "
         "zurückübersetzen.",
         "Offset → residues: one `seek`, read `seq_len` bytes, translate DIAMOND's letter code back into amino "
         "acids."),
        ("Kontrolle bei jedem Zugriff: hinter den Residuen steht die Kopfzeile des Satzes (9.1); die gesuchte "
         "Accession muss darin vorkommen, und `seq_len` muss `blast_seq.len` entsprechen. Sonst stimmt die "
         "Lücken-Tabelle nicht – Fehler statt falscher Sequenz.",
         "Check on every access: the record's header follows the residues (9.1); the requested accession must "
         "appear in it, and `seq_len` must equal `blast_seq.len`. Otherwise the gap table is wrong – error "
         "instead of a wrong sequence."),
    ])
    d.p("Kosten: zwei Zufallszugriffe auf sda5, Millisekunden. Die Sequenzen sind damit immer auf dem Stand der "
        "fortgeschriebenen .dmnd, auch für Accessions aus dem Tagesdelta. Während eines Appends ändert sich die "
        "Offset-Tabelle; das Skript nimmt deshalb dieselbe geteilte Sperre `/home/gh/.nr_dmnd.lock` wie der "
        "API-Worker.",
        "Cost: two random reads on sda5, milliseconds. The sequences always reflect the updated .dmnd, including "
        "accessions from the daily delta. The offset table changes during an append; the script therefore takes "
        "the same shared lock `/home/gh/.nr_dmnd.lock` as the API worker.")

    d.h2("Umstellung und Freigabe", "Migration and release")
    d.liste([
        ("`dmnd_getseq.py` bauen (nach Abschluss von `nr_build.py`; `blast_dmnd_luecke` und `idx_acc` liegen "
         "dann vor).",
         "Build `dmnd_getseq.py` (after `nr_build.py` has finished; `blast_dmnd_luecke` and `idx_acc` exist "
         "then)."),
        ("Abgleich: für eine Stichprobe von 100.000 zufälligen Accessions `blastdbcmd` gegen `dmnd_getseq.py`, "
         "alle Residuen identisch; dazu alle 43 Amyloid-Kandidaten.",
         "Cross-check: for a sample of 100,000 random accessions, `blastdbcmd` vs. `dmnd_getseq.py` must return "
         "identical residues; plus all 43 amyloid candidates."),
        ("`api.php` (acc) und `amylo_gpu.py` (`--acc`, `--kandidaten`) auf `dmnd_getseq.py` umstellen.",
         "Switch `api.php` (acc) and `amylo_gpu.py` (`--acc`, `--kandidaten`) to `dmnd_getseq.py`."),
        ("Danach `~/blastdb` löschen: 857 GB frei auf der System-NVMe (heute 78 % belegt). Löschen erst nach "
         "bestandenem Abgleich und ausdrücklicher Freigabe; ein Vollneubau lädt die nr ohnehin neu von NCBI.",
         "Then delete `~/blastdb`: 857 GB freed on the system NVMe (78 % used today). Delete only after the "
         "cross-check has passed and with explicit approval; a full rebuild downloads nr from NCBI anyway."),
        ("`nr_full.dmnd` danach von der HDD (sda5) auf die freie System-NVMe verschieben: DIAMOND streamt die "
         "ganze Datenbank je Suche, von der HDD dauert ein Durchgang über ~460 GB eine Stunde und mehr.",
         "Then move `nr_full.dmnd` from the HDD (sda5) to the freed system NVMe: DIAMOND streams the whole "
         "database per search, and one pass over ~460 GB from the HDD takes an hour or more."),
    ])

    d.h1("Mögliche nächste Stufe", "Possible next step")
    d.p("Falls DIAMOND bei sehr entfernten Homologien nicht ausreicht: Zwei-Stufen-Suche über Protein-Embeddings, "
        "die dasselbe Muster wiederverwendet, das bereits für die Wissensbasis (vec_-Tabellen in wagodb, MariaDB "
        "VECTOR mit HNSW-Index, Embedding-Server auf der P4) produktiv läuft: Sequenzen als Vektoren ablegen, per "
        "`VEC_DISTANCE_COSINE` vorfiltern, nur die Kandidaten anschließend exakt mit DIAMOND/BLAST nachprüfen. "
        "Die ESM-2-650M-Umgebung aus Abschnitt 8 liefert solche Embeddings bereits.",
        "If DIAMOND is not sensitive enough for very distant homologues: a two-stage search over protein "
        "embeddings, reusing the pattern already in production for the knowledge base (vec_ tables in wagodb, "
        "MariaDB VECTOR with HNSW index, embedding server on the P4): store sequences as vectors, prefilter with "
        "`VEC_DISTANCE_COSINE`, then check only the candidates exactly with DIAMOND/BLAST. The ESM-2 650M "
        "environment from section 8 already produces such embeddings.")


def AMYL_TEXT(d, amyl):
    """Deutung des Kandidatenlaufs (von Hand geschrieben, bezogen auf amyl_lauf.lauf_id = 1)."""
    if amyl["lauf"][0] != 1:
        d.p("Die folgende Tabelle stammt aus einem neueren Lauf; die Deutung unten bezog sich auf Lauf 1.",
            "The table below comes from a newer run; the interpretation was written for run 1.")
    d.liste([
        ("**Das Maximum je Protein trennt nicht.** Alle 43 liegen zwischen 0,88 und 0,97. Ohne Negativkontrollen "
         "(nicht amyloidogene Proteine gleicher Länge) sagt „max > 0,9“ nichts; `swissprot_titel.tsv` im selben "
         "Ordner ist der naheliegende Kontrollsatz.",
         "**The per-protein maximum does not discriminate.** All 43 lie between 0.88 and 0.97. Without negative "
         "controls (non-amyloidogenic proteins of similar length) “max > 0.9” means nothing; `swissprot_titel.tsv` "
         "in the same folder is the obvious control set."),
        ("**19 von 43 Maxima liegen in den ersten 30 Resten**, also im Signalpeptid (z.B. AApoAI Pos. 3, AB2M 5, "
         "ATTR 8, AIAPP 9). Hydrophobe 10-mere werden hoch bewertet, wie bei allen Aggregations-Vorhersagern. Die "
         "Kandidaten sind UniProt-Vorläufer; für Aussagen über Fibrillen gehört die reife Kette ausgewertet.",
         "**19 of 43 maxima lie within the first 30 residues**, i.e. in the signal peptide (e.g. AApoAI pos. 3, "
         "AB2M 5, ATTR 8, AIAPP 9). Hydrophobic 10-mers score high, as with every aggregation predictor. The "
         "candidates are UniProt precursors; statements about fibrils need the mature chain."),
        ("**Treffer auf bekannten Kernen:** Aβ (Pos. 705 `LMVGGVVIAT`, C-Terminus von Aβ42), α-Synuclein "
         "(70 `VVTGVTAVAQ`, NAC-Kern), Tau (619 `GGGSVQIVYK` mit PHF6 VQIVYK), FUS (140, Q/S/Y-reiche "
         "Low-Complexity-Domäne).",
         "**Hits on known cores:** Aβ (pos. 705 `LMVGGVVIAT`, C terminus of Aβ42), α-synuclein (70 `VVTGVTAVAQ`, "
         "NAC core), tau (619 `GGGSVQIVYK` containing PHF6 VQIVYK), FUS (140, Q/S/Y-rich low-complexity "
         "domain)."),
        ("**Daneben:** Sup35 (582, nicht in der N-terminalen Prion-Domäne), PrP (240, GPI-Signal statt Region "
         "106–126), Huntingtin (847, nicht der PolyQ-Abschnitt).",
         "**Misses:** Sup35 (582, not in the N-terminal prion domain), PrP (240, GPI signal instead of region "
         "106–126), huntingtin (847, not the polyQ stretch)."),
    ])
    d.p("Nächster Schritt: Kontrollsatz und reife Ketten (Signalpeptide aus UniProt abschneiden), dann "
        "Verteilungen statt Maxima vergleichen.",
        "Next step: control set and mature chains (signal peptides cut according to UniProt), then compare "
        "distributions instead of maxima.")


# ------------------------------------------------------------------ Bau

def bauen(lang, amyl, spike, bilder):
    dbg(f"{lang}: Text zusammenbauen")
    d = Doku(lang, bilder)
    inhalt(d, amyl, spike)
    kopf = KOPF % {
        "babel": "ngerman" if lang == "de" else "english",
        "titel_pdf": "BLAST zu DIAMOND: Konzept" if lang == "de" else "BLAST to DIAMOND: concept",
        "erzeugt": "erzeugt von" if lang == "de" else "generated by",
        "seite": "Seite" if lang == "de" else "Page",
        "generator": esc_code(GENERATOR),
    }
    tex = kopf + "\n\n".join(d.teile) + "\n\\end{document}\n"
    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, "doku.tex"), "w", encoding="utf-8").write(tex)
        for durchlauf in (1, 2):                        # zweimal: Inhaltsverzeichnis, longtable-Breiten
            t0 = time.time()
            try:
                r = subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "doku.tex"],
                                   cwd=tmp, capture_output=True, text=True, errors="replace", timeout=300)
            except subprocess.TimeoutExpired:
                sys.exit(f"pdflatex ({lang}, Durchlauf {durchlauf}) ueberschritt 300s")
            dbg(f"{lang}: pdflatex {durchlauf}/2 in {time.time() - t0:.1f}s (rc {r.returncode})")
        log = open(os.path.join(tmp, "doku.log"), errors="replace").read()
        if r.returncode != 0 or "Unicode character" in log:
            shutil.copy(os.path.join(tmp, "doku.tex"), f"/tmp/blast_diamond_{lang}.tex")
            fehler = [z for z in log.splitlines() if z.startswith("!") or "Unicode character" in z]
            sys.exit(f"pdflatex ({lang}) fehlgeschlagen, Quelle in /tmp/blast_diamond_{lang}.tex:\n"
                     + "\n".join(fehler[:10]))
        ueber = len(re.findall(r"Overfull \\hbox \((\d+\.\d+)pt", log))
        ziel = os.path.join(ZIEL, DATEI[lang])
        shutil.copy(os.path.join(tmp, "doku.pdf"), ziel)
        dbg(f"{lang}: fertig -> {ziel} ({os.path.getsize(ziel) / 1024:.0f} kB)")
        print(f"{ziel}" + (f"  ({ueber} Overfull-Boxen)" if ueber else ""))


if __name__ == "__main__":
    dbg(f"Start, Ziel {ZIEL}")
    amyl = amyl_ergebnis()
    dbg(f"amyl_ergebnis: {len(amyl['seqs']) if amyl else 0} Zeilen")
    spike = spike_test()
    dbg(f"spike_test: {'da' if spike else 'noch nicht gerechnet'}")
    with tempfile.TemporaryDirectory() as er_tmp:
        try:
            sys.path.insert(0, os.path.expanduser("~/python"))
            import blast_db
            import pymysql
            cc = pymysql.connect(**blast_db.cfg()).cursor()
            cc.execute("SET SESSION max_statement_time = 60")
            bilder = er_diagramme(cc, er_tmp)
            dbg(f"ER-Diagramme fertig: {sorted(bilder)}")
        except Exception as e:
            print(f"ER-Diagramme: nicht erzeugt ({e})", file=sys.stderr)
            bilder = {}
        for lang in ("de", "en"):
            bauen(lang, amyl, spike, bilder)
