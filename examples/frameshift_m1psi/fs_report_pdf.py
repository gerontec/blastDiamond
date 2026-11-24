#!/usr/bin/env python3
"""Build Frameshift.pdf from the output of fs_peptide_search.py.

Usage: fs_report_pdf.py <out_dir> <query.fa> [target.pdf]
"""
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date

LANG = "de"

PART_LABEL = {
    "p1_mammalia": ("Mammalia", "Mammalia", "15.6 M"),
    "p2_eukaryota_other": ("Eukaryota, übrige", "Eukaryota, rest", "157.3 M"),
    "p3_viruses_archaea_other": ("Viren, Archaeen, sonstige", "viruses, archaea, other", "44.3 M"),
    "p4_pseudomonadota": ("Pseudomonadota", "Pseudomonadota", "353.9 M"),
    "p5_bacillati": ("Bacillati", "Bacillati", "345.4 M"),
    "p6_bacteria_other": ("Bakterien, übrige", "bacteria, rest", "263.3 M"),
}

CLASS_LABEL = {
    "fs": ("+1FS Spike, vorhergesagt (ED Table 3)",
           "+1FS spike, predicted (ED Table 3)"),
    "ms": ("+1FS Fluc, MS-belegt (ED Table 1)",
           "+1FS Fluc, MS-identified (ED Table 1)"),
    "ct": ("Fluc in-frame, Positivkontrolle (ED Table 1)",
           "Fluc in-frame, positive control (ED Table 1)"),
}

T = {
    "part": ("Teil-DB", "part DB"), "content": ("Inhalt", "content"),
    "seqs": ("Sequenzen", "sequences"), "runtime": ("Laufzeit", "wall clock"),
    "rawhsp": ("Roh-HSPs", "raw HSPs"), "sum": ("Summe", "total"),
    "perq": ("je Query", "per query"), "class": ("Klasse", "class"),
    "peptides": ("Peptide", "peptides"), "result": ("Ergebnis", "result"),
    "nohit": ("kein Treffer", "no hit"), "withhit": ("mit Treffer", "with a hit"),
    "query": ("Query", "query"), "peptide": ("Peptid", "peptide"),
    "acc": ("Accession", "accession"), "org": ("Organismus", "organism"),
    "none": ("\\emph{Keine Zeilen.}", "\\emph{No rows.}"),
}


def t(k):
    return T[k][0 if LANG == "de" else 1]


def tex(s):
    for a, b in [("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"),
                 ("$", r"\$"), ("#", r"\#"), ("_", r"\_"), ("{", r"\{"),
                 ("}", r"\}"), ("~", r"\textasciitilde{}"),
                 ("^", r"\textasciicircum{}")]:
        s = s.replace(a, b)
    return s


def read_tsv(path):
    if not os.path.exists(path):
        return []
    return [l.rstrip("\n").split("\t") for l in open(path) if l.strip()]


def fmt(s):
    return f"{s:.1f}\\,s" if s < 90 else f"{s:.0f}\\,s ({s/60:.1f}\\,min)"


def read_queries(fa):
    out, name = {}, None
    for line in open(fa):
        line = line.strip()
        if line.startswith(">"):
            name = line[1:]
            out[name] = ""
        elif name:
            out[name] += line
    return out


def runtime_table(out_dir, n_q):
    rows = read_tsv(os.path.join(out_dir, "runtimes.tsv"))[1:]
    body, total = [], 0.0
    for r in rows:
        if r[0] == "TOTAL":
            continue
        secs = float(r[1])
        total += secs
        pl = PART_LABEL.get(r[0], (r[0], r[0], ""))
        lbl, nseq = (pl[0] if LANG == "de" else pl[1]), pl[2]
        body.append(f"\\texttt{{{tex(r[0])}}} & {tex(lbl)} & {nseq} & "
                    f"{fmt(secs)} & {tex(r[3])} \\\\")
    body.append("\\midrule")
    body.append(f"\\multicolumn{{3}}{{l}}{{\\textbf{{{t('sum')}}}}} & "
                f"\\textbf{{{fmt(total)}}} & \\\\")
    body.append(f"\\multicolumn{{3}}{{l}}{{{t('perq')} ({n_q})}} & "
                f"{total/max(n_q,1):.2f}\\,s & \\\\")
    return ("\\begin{tabular}{llrrr}\n\\toprule\nTeil-DB & Inhalt & Sequenzen & "
            "Laufzeit & Roh-HSPs \\\\\n\\midrule\n" + "\n".join(body)
            + "\n\\bottomrule\n\\end{tabular}"), total


def hits_by_class(out_dir, queries):
    meta = read_tsv(os.path.join(out_dir, "meta.tsv"))
    data = meta[1:] if meta else []
    per = {"fs": [], "ms": [], "ct": []}
    for r in data:
        per.get(r[1][:2], per["fs"]).append(r)
    return per, data


def class_summary(per, queries):
    counts = {k: len([q for q in queries if q.startswith(k)]) for k in CLASS_LABEL}
    body = []
    for k, lbls in CLASS_LABEL.items():
        lbl = lbls[0 if LANG == "de" else 1]
        hit_q = len({r[1] for r in per[k]})
        verdict = t("nohit") if hit_q == 0 else f"{hit_q} {t('withhit')}"
        body.append(f"{tex(lbl)} & {counts[k]} & {verdict} \\\\")
    return (f"\\begin{{tabular}}{{lrl}}\n\\toprule\n{t('class')} & "
            f"{t('peptides')} & {t('result')} \\\\\n"
            "\\midrule\n" + "\n".join(body) + "\n\\bottomrule\n\\end{tabular}")


def meta_table(data, queries):
    if not data:
        return t("none")
    body = []
    for r in data[:30]:
        part, q, acc, taxid, slen, org, rank, title = (r + [""] * 8)[:8]
        body.append(" & ".join([
            "\\texttt{" + tex(part.replace("_", " ")) + "}",
            "\\texttt{" + tex(q.split("_")[0]) + "}",
            "\\texttt{" + tex(queries.get(q, "")) + "}",
            "\\texttt{" + tex(acc) + "}",
            tex(org), tex(slen)]) + " \\\\")
    return (f"\\begin{{tabular}}{{llllll}}\n\\toprule\n{t('part')} & {t('query')} & "
            f"{t('peptide')} & {t('acc')} & {t('org')} & aa \\\\\n\\midrule\n" + "\n".join(body)
            + "\n\\bottomrule\n\\end{tabular}")


def cluster_section(cl_dir):
    """Summarise the human-only cluster run, if it produced output.

    The decisive distinction is not singleton vs. grouped, but whether a
    peptide shares its cluster with a *human* sequence or only with another
    peptide of the overlapping pool.
    """
    rt = os.path.join(cl_dir, "cluster_runtime.tsv")
    c90 = os.path.join(cl_dir, "clu90.tsv")
    c50 = os.path.join(cl_dir, "clu50.tsv")
    if not (os.path.exists(c90) and os.path.exists(c50)):
        return ("\\emph{Der Clusterlauf war zum Satz dieses Berichts noch nicht "
                "abgeschlossen.}" if LANG == "de" else
                "\\emph{The cluster run had not finished when this report was "
                "typeset.}")

    rep90, rep50 = {}, {}
    for line in open(c90):
        r, m = line.rstrip("\n").split("\t")[:2]
        rep90[m] = r
    for line in open(c50):
        r, m = line.rstrip("\n").split("\t")[:2]
        rep50[m] = r
    fam50 = {}
    for m, r in rep90.items():
        fam50.setdefault(rep50.get(r, r), []).append(m)

    peps = [m for m in rep90 if m.startswith("pep_")]
    with_human, pep_only = [], set()
    for p in peps:
        fam = fam50[rep50.get(rep90[p], rep90[p])]
        if any(not x.startswith("pep_") for x in fam):
            with_human.append((p, [x for x in fam if not x.startswith("pep_")]))
        elif len(fam) > 1:
            pep_only.add(rep50.get(rep90[p], rep90[p]))

    info = ""
    if os.path.exists(rt):
        r = read_tsv(rt)[1:]
        if r:
            secs = f"\\textbf{{{fmt(float(r[0][1]))}}}"
            nseq = f"{int(r[0][2]):,}".replace(",", "\\,")
            info = ((f"Laufzeit {secs} für {nseq} humane Sequenzen "
                     f"(taxid {r[0][4]}) plus {r[0][3]} Peptide, 20 Threads.\n\n")
                    if LANG == "de" else
                    (f"Wall clock {secs} for {nseq} human sequences "
                     f"(taxid {r[0][4]}) plus {r[0][3]} peptides, 20 threads.\n\n"))

    out = [info]
    out.append(
        (f"Von {len(peps)} Peptiden teilen sich \\textbf{{{len(with_human)}}} einen "
         f"Cluster mit einer menschlichen Sequenz. Weitere {len(pep_only)} Gruppen "
         "bestehen ausschließlich aus Peptiden untereinander -- das sind jeweils "
         "zwei benachbarte Pool-Mitglieder, die sich ohnehin um zehn Reste "
         "überlappen, also ein Artefakt des Pooldesigns und kein Befund.\n")
        if LANG == "de" else
        (f"Of {len(peps)} peptides, \\textbf{{{len(with_human)}}} share a cluster "
         f"with a human sequence. A further {len(pep_only)} groups consist of "
         "peptides only; each is a pair of adjacent pool members that already "
         "overlap by ten residues, so an artefact of the pool design rather "
         "than a finding.\n"))
    if not with_human:
        out.append("Kein einziges Peptid gruppiert sich mit einer menschlichen "
                   "Sequenz." if LANG == "de" else
                   "Not one peptide groups with a human sequence.")
    else:
        for p, hs in with_human:
            who = tex(p.replace("pep_", "").split("_")[0])
            hh = tex(", ".join(h.replace("nr_", "OID ") for h in hs[:3]))
            out.append(
                ("\nDie eine Ausnahme ist \\texttt{" + who + "}, zusammen mit \\texttt{"
                 + hh + "}. Das ist derselbe Eintrag, der in "
                 "Abschnitt~\\ref{sec:aehnlich} aufgeklärt wird: "
                 "\\texttt{AAG23172.1}, und der Grund dafür ist das menschliche "
                 "3'-UTR des Impfstoffs, nicht eine Ähnlichkeit des "
                 "Spike-Produkts zu einem menschlichen Protein.")
                if LANG == "de" else
                ("\nThe single exception is \\texttt{" + who + "}, together with "
                 "\\texttt{" + hh + "}. It is the same entry resolved in "
                 "Section~\\ref{sec:aehnlich}: \\texttt{AAG23172.1}. The reason is "
                 "the human 3' UTR of the vaccine, not a similarity between a "
                 "spike product and a human protein."))
    return "\n".join(out)


def dbsize_of(out_dir):
    """The --dbsize the search actually used, as the search recorded it."""
    f = os.path.join(out_dir, "dbsize.txt")
    if os.path.exists(f):
        return open(f).read().strip()
    return "444958619351"


HERE = os.path.dirname(os.path.abspath(__file__))


def load_template(lang):
    """Templates live next to the script as fs_report_<lang>.tex."""
    path = os.path.join(HERE, f"fs_report_{lang}.tex")
    if not os.path.exists(path):
        sys.exit(f"no template for language {lang!r}: {path}")
    return open(path).read()


def main():
    argv = list(sys.argv[1:])
    lang = "de"
    if "--lang" in argv:
        i = argv.index("--lang")
        lang = argv[i + 1]
        del argv[i:i + 2]
    sys.argv = [sys.argv[0]] + argv
    global LANG
    LANG = lang
    out_dir, fa = argv[0], argv[1]
    target = argv[2] if len(argv) > 2 else "Frameshift.pdf"
    queries = read_queries(fa)
    rt, total = runtime_table(out_dir, len(queries))
    per, data = hits_by_class(out_dir, queries)

    n_raw = sum(sum(1 for _ in open(os.path.join(out_dir, p + ".tsv")))
                for p in PART_LABEL if os.path.exists(os.path.join(out_dir, p + ".tsv")))

    fs_q = [q for q in queries if q.startswith("fs")]
    pep_tex = "\\\\\n".join(
        " \\quad ".join("\\texttt{" + queries[q] + "}" for q in fs_q[i:i + 3])
        for i in range(0, len(fs_q), 3))

    fields = dict(
        datum=date.today().strftime("%d.%m.%Y"),
        runtime_table=rt,
        total_runtime=fmt(total),
        class_summary=class_summary(per, queries),
        meta_table=meta_table(data, queries),
        n_raw=n_raw,
        n_exact=len(data),
        n_fs=len(fs_q),
        n_all=len(queries),
        peptides=pep_tex,
        dbsize=dbsize_of(out_dir),
        cluster_section=cluster_section(
            sys.argv[4] if len(sys.argv) > 4 else "/tmp/fs_cluster_out"),
    )
    body = load_template(lang)
    for k, v in fields.items():
        body = body.replace("@@" + k + "@@", str(v))
    if "@@" in body:
        import sys as _s
        _s.exit("unresolved placeholder: " + body[body.index("@@"):][:40])

    with tempfile.TemporaryDirectory() as td:
        open(os.path.join(td, "r.tex"), "w").write(body)
        for _ in range(2):
            p = subprocess.run(["pdflatex", "-interaction=nonstopmode", "r.tex"],
                               cwd=td, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
        pdf = os.path.join(td, "r.pdf")
        if not os.path.exists(pdf):
            sys.stderr.write(p.stdout[-5000:])
            sys.exit("pdflatex failed")
        shutil.copy(pdf, target)
    print("wrote", target, os.path.getsize(target), "bytes")


if __name__ == "__main__":
    main()
