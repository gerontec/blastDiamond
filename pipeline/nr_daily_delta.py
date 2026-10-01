#!/usr/bin/env python3
"""Taegliches Delta fuer nr_full.dmnd und blast_* aus
   GenBank daily-nc  (ncMMDD.gnp.gz, GenPept, ~180 Tage vorgehalten) und
   RefSeq daily      (rsnc.MMDD.YYYY.gpff.gz, GenPept, ~21 Tage vorgehalten).
Since 2026-10-01 this is the only nr update path (diamond_append.py and its full-release
downloads are retired). It also covers the nr sources the daily files miss:
   PDB        pdb_seqres.txt.gz (wwPDB, ~67 MB, weekly), protein chains only
   Swiss-Prot uniprot_sprot.fasta.gz (UniProt, ~94 MB, every ~8 weeks)
A single-file source counts as a new "daily file" whenever its Last-Modified date changes.
taxdump (nodes/names) is refreshed when older than TAX_MAX_AGE_D days.

Neu ist ein Protein, dessen Sequenz (blake2b-64 der Grossbuchstaben) weder in nr noch in
einem frueheren Delta vorkommt - NCBI fuehrt identische Proteine in nr nicht einzeln, ein
Abgleich nach Accession wuerde massenhaft Duplikate anhaengen.
Alle offenen Tagesdateien seit dem letzten nr-Release werden in EINEM Append verarbeitet.
"""
import os, re, sys, json, gzip, time, fcntl, shutil, hashlib, subprocess, urllib.request, smtplib
from email.mime.text import MIMEText
import numpy as np

import blast_db, pymysql

E = os.environ.get
PREFIX = E("BLAST_PREFIX", "blast_")
DMND = E("DMND", "/home/gh/diamond/nr_full.dmnd")
DIAMOND = E("DIAMOND_APPEND_BIN", os.path.expanduser("~/src/diamond/build/diamond"))
TAXDIR = E("TAXDIR", "/mnt/archive/taxdump")
WORK = E("WORK", "/home/gh/nr_daily")
SEQ_CACHE = E("SEQ_CACHE", "/mnt/archive/nr_seq_hashes.npy")
ACC_CACHE = E("ACC_CACHE", "/mnt/archive/nr_acc_hashes.npy")
LOG = E("DELTA_LOG", os.path.expanduser("~/python/nr_daily_delta.log"))
LOCK = E("NR_LOCK", "/home/gh/.nr_dmnd.lock")
LOCK_WAIT_H = float(E("LOCK_WAIT_H", "12"))   # Sperre teilen sich alle nr-Werkzeuge (z.B. Vollneubau)
TEST_FILES = E("TEST_FILES")       # "quelle|pfad|YYYY-MM-DD,..." statt NCBI-Listing
NO_MAIL = E("NO_MAIL") == "1"
SOURCES = {
    "genbank-daily": ("https://ftp.ncbi.nlm.nih.gov/genbank/daily-nc/", r"nc\d{4}\.gnp\.gz"),
    "refseq-daily": ("https://ftp.ncbi.nlm.nih.gov/refseq/daily/", r"rsnc\.\d{4}\.\d{4}\.gpff\.gz"),
}
SINGLE_SOURCES = {     # whole file, new version = new Last-Modified date
    "pdb-seqres": "https://files.wwpdb.org/pub/pdb/derived_data/pdb_seqres.txt.gz",
    "swissprot": "https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/complete/uniprot_sprot.fasta.gz",
}
TAX_URL = "https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz"
TAX_MAX_AGE_D = float(E("TAX_MAX_AGE_D", "30"))
MAIL_TO = E("MAIL_TO", "")               # leer = keine Mail
SMTP_HOST = E("SMTP_HOST", "localhost")
MAIL_FROM = E("MAIL_FROM", "nr-update@localhost")
MAIL_EHLO = E("MAIL_EHLO", "localhost")


def log(msg):
    line = time.strftime("[%F %T] ") + msg
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def mail(subject, body):
    if NO_MAIL or not MAIL_TO:
        return
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, MAIL_FROM, MAIL_TO
    s = smtplib.SMTP(SMTP_HOST, 25, timeout=30)
    s.ehlo(MAIL_EHLO)
    s.sendmail(msg["From"], [MAIL_TO], msg.as_string())
    s.quit()


def h64(b):
    return int.from_bytes(hashlib.blake2b(b, digest_size=8).digest(), "little")


def in_sorted(arr, hashes):
    if len(arr) == 0 or len(hashes) == 0:
        return np.zeros(len(hashes), dtype=bool)
    h = np.asarray(hashes, dtype=np.uint64)
    idx = np.searchsorted(arr, h)
    idx[idx == len(arr)] = 0
    return arr[idx] == h


def parse_genpept(path):
    """-> (acc.version, titel, taxid, sequenz-bytes GROSS, LOCUS-Datum YYYY-MM-DD)"""
    acc = title = datum = None
    taxid, seq, in_def, in_seq = 0, [], False, False
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("//"):
                if acc:
                    yield acc, (title or "").rstrip("."), taxid, "".join(seq).upper().encode(), datum
                acc = title = datum = None
                taxid, seq, in_def, in_seq = 0, [], False, False
            elif in_seq:
                seq.append("".join(line.split()[1:]))
            elif line.startswith("LOCUS"):
                datum = time.strftime("%Y-%m-%d", time.strptime(line.split()[-1], "%d-%b-%Y"))
            elif line.startswith("VERSION"):
                in_def = False
                acc = line.split()[1]
            elif line.startswith("DEFINITION"):
                title, in_def = line[12:].strip(), True
            elif in_def and line.startswith("            "):
                title += " " + line.strip()
            elif line.startswith("ORIGIN"):
                in_seq = True
            else:
                if line[:1].isalpha():
                    in_def = False
                if taxid == 0 and '/db_xref="taxon:' in line:
                    taxid = int(line.split("taxon:")[1].split('"')[0])


def read_fasta(path):
    """-> (header without '>', sequence str) from a gzip FASTA"""
    head, seq = None, []
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith(">"):
                if head is not None:
                    yield head, "".join(seq)
                head, seq = line[1:].rstrip("\n"), []
            else:
                seq.append(line.strip())
    if head is not None:
        yield head, "".join(seq)


def parse_pdb_seqres(path, datum):
    """'>101m_A mol:protein length:154  MYOGLOBIN' -> acc 101M_A like nr; nucleic acids skipped,
    no taxid in this file (0)"""
    for head, seq in read_fasta(path):
        parts = head.split(None, 3)
        if len(parts) < 2 or parts[1] != "mol:protein":
            continue
        pdb, _, chain = parts[0].partition("_")
        yield f"{pdb.upper()}_{chain}", parts[3] if len(parts) > 3 else "", 0, seq.upper().encode(), datum


def parse_uniprot(path, datum):
    """'>sp|P69905|HBA_HUMAN Hemoglobin subunit alpha OS=Homo sapiens OX=9606 GN=HBA1 PE=1 SV=2'
    -> acc P69905.2 (version = SV, as in nr), title before ' OS=', taxid from OX="""
    for head, seq in read_fasta(path):
        ids, _, desc = head.partition(" ")
        acc = ids.split("|")[1] if ids.count("|") >= 2 else ids
        sv = re.search(r" SV=(\d+)", desc)
        ox = re.search(r" OX=(\d+)", " " + desc)
        yield (f"{acc}.{sv.group(1)}" if sv else acc), desc.split(" OS=")[0], \
            int(ox.group(1)) if ox else 0, seq.upper().encode(), datum


PARSERS = {
    "genbank-daily": lambda p, d: parse_genpept(p),
    "refseq-daily": lambda p, d: parse_genpept(p),
    "pdb-seqres": parse_pdb_seqres,
    "swissprot": parse_uniprot,
}


def listing():
    """Offene Tagesdateien: [(quelle, url_oder_pfad, datei, datum)] chronologisch."""
    if TEST_FILES:
        out = []
        for spec in TEST_FILES.split(","):
            q, p, d = spec.split("|")
            out.append((q, p, os.path.basename(p), d))
        return out
    out = []
    for q, (base, pat) in SOURCES.items():
        html = urllib.request.urlopen(base, timeout=60).read().decode()
        for name, d in re.findall(rf'href="({pat})">[^<]*</a>\s+(\d{{4}}-\d{{2}}-\d{{2}})', html):
            out.append((q, base + name, name, d))
    for q, url in SINGLE_SOURCES.items():
        req = urllib.request.Request(url, method="HEAD")
        lm = urllib.request.urlopen(req, timeout=60).headers["Last-Modified"]
        d = time.strftime("%Y-%m-%d", time.strptime(lm, "%a, %d %b %Y %H:%M:%S GMT"))
        out.append((q, url, os.path.basename(url), d))
    return sorted(set(out), key=lambda x: (x[3], x[0], x[2]))


def check_gaps(files, done_db):
    """Daily files are only kept for a limited time (RefSeq ~21 days). Now that the full release
    no longer fills holes, a gap between the last imported and the oldest listed file is lost data."""
    warn = []
    for q in SOURCES:
        last = max((d for qq, _, d in done_db if qq == q), default=None)
        avail = min((x[3] for x in files if x[0] == q), default=None)
        if last and avail and time.mktime(time.strptime(avail, "%Y-%m-%d")) - \
                time.mktime(time.strptime(last, "%Y-%m-%d")) > 86400 * 1.5:
            warn.append(f"{q}: last imported {last}, oldest still available {avail}")
    return warn


def refresh_taxdump():
    """nodes.dmp/names.dmp for diamond --taxonnodes/--taxonnames; formerly refreshed by
    diamond_append.py with each nr release"""
    nodes = os.path.join(TAXDIR, "nodes.dmp")
    if os.path.exists(nodes) and time.time() - os.path.getmtime(nodes) < TAX_MAX_AGE_D * 86400:
        return
    tgz = os.path.join(TAXDIR, "taxdump.tar.gz")
    want = urllib.request.urlopen(TAX_URL + ".md5", timeout=60).read().decode().split()[0]
    urllib.request.urlretrieve(TAX_URL, tgz + ".part")
    h = hashlib.md5()
    with open(tgz + ".part", "rb") as f:
        for b in iter(lambda: f.read(1 << 24), b""):
            h.update(b)
    if h.hexdigest() != want:
        os.remove(tgz + ".part")
        raise RuntimeError("taxdump.tar.gz md5 mismatch")
    os.replace(tgz + ".part", tgz)
    subprocess.run(["tar", "-xzf", "taxdump.tar.gz", "nodes.dmp", "names.dmp"], cwd=TAXDIR, check=True)
    os.utime(nodes)                               # tar keeps NCBI's mtime, age counts from download
    log("taxdump (nodes/names) refreshed")


def merge_in_datei(pfad, neu, block=1 << 26):
    """Neue Hashes in eine sortierte .npy einsortieren, blockweise: die alte Datei bleibt mmap,
    die neue entsteht per open_memmap. RAM ~ 2 Bloecke statt union1d ueber alles (nr_acc_hashes.npy
    15,5 GB -> union1d ~47 GB, OOM-Kill am 30.09.2026). Schon enthaltene Hashes werden uebersprungen."""
    alt = np.load(pfad, mmap_mode="r")
    neu = np.unique(np.asarray(neu, dtype=np.uint64))
    neu = neu[~in_sorted(alt, neu)]
    if not len(neu):
        return 0
    ins = np.searchsorted(alt, neu)
    tmp = pfad + ".tmp.npy"
    out = np.lib.format.open_memmap(tmp, mode="w+", dtype=np.uint64, shape=(len(alt) + len(neu),))
    o, n = 0, len(alt)
    for a in range(0, max(n, 1), block):
        b = min(a + block, n)
        sel = neu[(ins >= a) & ((ins < b) | (b == n))]
        teil = np.concatenate([alt[a:b], sel])
        teil.sort()
        out[o:o + len(teil)] = teil
        o += len(teil)
    assert o == len(out)
    out.flush()
    del out, alt
    os.replace(tmp, pfad)
    return len(neu)


def alter_lauf_abschliessen(state, done_db):
    """WORK stammt von einem frueheren Lauf. Stehen alle seine Tagesdateien schon in blast_release,
    war der Metadaten-Commit durch und der Lauf starb erst danach (Hash-Cache/Aufraeumen). Dann die
    Sequenz-Hashes seiner .fa-Dateien in SEQ_CACHE nachtragen (union, mehrfach harmlos) und WORK leeren.
    Sonst passt nr_full.dmnd evtl. nicht zu MariaDB -> weiter von Hand pruefen."""
    alt = [x.split(":") for x in state["key"].split("|")]
    offen = [f"{q}:{n}" for q, n, d in alt if (q, n, d) not in done_db]
    if offen:
        raise RuntimeError(f"{WORK} gehoert zu einem anderen Lauf, dessen Metadaten fehlen ({', '.join(offen[:5])}), "
                           "bitte pruefen/aufraeumen")
    hashes, acc_h = [], []
    for q, n, d in alt:
        ac = os.path.join(WORK, f"{q}_{d}_{n}.acc.tsv")
        if os.path.exists(ac):
            acc_h += [h64(l.split("\t")[2].encode()) for l in open(ac, encoding="ascii", errors="replace")]
        fa = os.path.join(WORK, f"{q}_{d}_{n}.fa")
        if os.path.exists(fa):
            seq = []
            for line in open(fa):
                if line.startswith(">"):
                    if seq:
                        hashes.append(h64("".join(seq).encode()))
                    seq = []
                else:
                    seq.append(line.strip())
            if seq:
                hashes.append(h64("".join(seq).encode()))
    n_seq = merge_in_datei(SEQ_CACHE, hashes) if hashes else 0
    n_acc = merge_in_datei(ACC_CACHE, acc_h) if acc_h and os.path.exists(ACC_CACHE) else 0
    log(f"Alter Lauf ({len(alt)} Tagesdateien) war schon eingetragen: {len(hashes)} Sequenz-/{len(acc_h)} "
        f"Accession-Hashes geprueft, {n_seq}/{n_acc} nachgetragen, {WORK} geleert")
    shutil.rmtree(WORK)
    os.makedirs(WORK)
    return {}


def main():
    os.makedirs(WORK, exist_ok=True)
    lock = open(LOCK, "w")
    t_lock = time.time()
    while True:                                   # warten statt aufgeben, sonst faellt der Tag aus
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.time() - t_lock > LOCK_WAIT_H * 3600:
                log(f"Anderer nr-Update laeuft seit ueber {LOCK_WAIT_H} h, Ende")
                return
            if time.time() - t_lock < 1:
                log("Anderer nr-Update laeuft, warte auf die Sperre")
            time.sleep(60)
    if time.time() - t_lock > 60:
        log(f"Sperre nach {(time.time() - t_lock) / 60:.0f} min erhalten")
    c = pymysql.connect(**blast_db.cfg(), autocommit=True, local_infile=True).cursor()
    c.execute(f"SELECT nr_datum, dmnd_hash, meta_importiert FROM {PREFIX}release WHERE quelle='nr' "
              "ORDER BY nr_datum DESC LIMIT 1")
    nr = c.fetchone()
    if not nr or nr[1] is None or nr[2] is None:
        log("Erstaufbau noch nicht abgeschlossen, Ende")
        return
    if not os.path.exists(SEQ_CACHE):
        log(f"{SEQ_CACHE} fehlt, Ende")
        return
    if os.path.exists(DMND + ".append_backup"):
        raise RuntimeError(f"{DMND}.append_backup existiert: frueherer Append unvollstaendig")

    c.execute(f"SELECT quelle, datei, DATE(nr_datum) FROM {PREFIX}release WHERE quelle<>'nr'")
    done_db = {(q, d, str(t)) for q, d, t in c.fetchall()}
    alt_file = os.path.join(WORK, "state.json")
    if os.path.exists(alt_file):                  # abgebrochener, aber schon eingetragener Lauf: erst fertig machen
        alt_state = json.load(open(alt_file))
        if alt_state.get("key") and all(tuple(x.split(":")) in done_db for x in alt_state["key"].split("|")):
            alter_lauf_abschliessen(alt_state, done_db)
    start = str(nr[0])[:10]
    t_list = time.time()
    files = listing()
    log(f"step listing: {len(files)} files listed in {time.time() - t_list:.1f}s")
    gaps = check_gaps(files, done_db)
    if gaps:
        log("WARNING gap in daily files: " + "; ".join(gaps))
        try:
            mail("nr-Tagesdelta: Luecke in den Tagesdateien", "\n".join(gaps))
        except Exception:
            pass
    pending = [x for x in files if x[3] >= start and (x[0], x[2], x[3]) not in done_db]
    if not pending:
        log(f"Keine offenen Tagesdateien seit nr-Stand {start}, Ende")
        return
    log(f"=== {len(pending)} offene Tagesdateien seit {start} ===")

    state_file = os.path.join(WORK, "state.json")
    state = json.load(open(state_file)) if os.path.exists(state_file) else {}
    key = "|".join(f"{q}:{n}:{d}" for q, _, n, d in pending)
    if state.get("key", key) != key:
        state = alter_lauf_abschliessen(state, done_db)
    state["key"] = key

    known = np.load(SEQ_CACHE, mmap_mode="r")
    seen, per_file = {}, []
    for q, src, name, d in pending:
        tag = f"{q}_{d}_{name}"
        base = os.path.join(WORK, tag)
        path = base + ".gz"
        t_dl = time.time()
        if not src.startswith("http"):
            shutil.copy(src, path)
        elif not os.path.exists(path):
            urllib.request.urlretrieve(src, path + ".part")
            os.replace(path + ".part", path)
        t0 = time.time()
        dl_s = t0 - t_dl
        recs = [r for r in PARSERS[q](path, d) if r[3]]
        hashes = [h64(r[3]) for r in recs]
        in_nr = in_sorted(known, hashes)
        n_new = n_dup_nr = n_dup_run = letters = 0
        with open(base + ".fa", "w") as fa, open(base + ".map", "w") as fm, \
                open(base + ".seq.tsv", "w", encoding="utf-8") as fs, open(base + ".acc.tsv", "w", encoding="ascii", errors="replace") as fc, \
                open(base + ".ncbi.tsv", "w") as fn:
            for (acc, title, taxid, seq, datum), h, dup in zip(recs, hashes, in_nr):
                if dup:
                    n_dup_nr += 1
                    continue
                if h in seen:
                    n_dup_run += 1
                    continue
                seen[h] = acc
                title = title.replace("\t", " ")
                fa.write(f">{acc} {title}\n")
                s = seq.decode()
                for i in range(0, len(s), 80):
                    fa.write(s[i:i + 80] + "\n")
                fm.write(f"{acc}\t{taxid}\n")
                fs.write(f"{n_new}\t{len(s)}\t1\t{acc} {title}\n")
                fc.write(f"{n_new}\t0\t{acc}\t{taxid}\n")
                fn.write(f"{n_new}\t{datum}\n")
                n_new += 1
                letters += len(s)
        os.remove(path)
        st = {"quelle": q, "datei": name, "datum": d, "records": len(recs), "neu": n_new,
              "dup_nr": n_dup_nr, "dup_lauf": n_dup_run, "letters": letters, "base": base}
        per_file.append(st)
        log(f"{q} {name} ({d}): {len(recs)} Proteine, {n_new} neu, {n_dup_nr} schon in nr, "
            f"{n_dup_run} doppelt im Delta ({time.time() - t0:.0f}s, download {dl_s:.0f}s)")

    total_new = sum(s["neu"] for s in per_file)
    if total_new and not state.get("dmnd_done"):
        t_tax = time.time()
        refresh_taxdump()
        log(f"step taxdump check/refresh: {time.time() - t_tax:.1f}s")
        t_mk = time.time()
        mp = os.path.join(WORK, "delta_map.tsv")
        with open(mp, "w") as out:
            out.write("accession.version\ttaxid\n")
            for s in per_file:
                out.write(open(s["base"] + ".map").read())
        log(f"=== diamond makedb --append: {total_new} neue Sequenzen ===")
        cmd = f"cat {' '.join(s['base'] + '.fa' for s in per_file)} | {DIAMOND} makedb --append --db {DMND} " \
              f"--taxonmap {mp} --taxonnodes {TAXDIR}/nodes.dmp --taxonnames {TAXDIR}/names.dmp --threads 20 --no-parse-seqids"
        r = subprocess.run(["bash", "-o", "pipefail", "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        with open(LOG, "a") as f:
            f.write(r.stdout[-3000:])
        if r.returncode != 0:
            raise RuntimeError("diamond makedb --append fehlgeschlagen (DB per Rollback unveraendert)")
        state["dmnd_hash"] = [l for l in r.stdout.splitlines() if "Database hash" in l][0].split()[-1]
        state["dmnd_sequences"] = int([l for l in r.stdout.splitlines() if "Database sequences" in l][0].split()[-1])
        state["dmnd_done"] = True
        json.dump(state, open(state_file, "w"))
        log(f"Append fertig: {state['dmnd_sequences']} Sequenzen, Hash {state['dmnd_hash']} "
            f"(step makedb --append: {time.time() - t_mk:.0f}s)")

    # Metadaten: je Tagesdatei eine Release-Zeile und ein OID-Bereich - alles in EINER Transaktion,
    # damit MariaDB nie halb zum dmnd passt (Abbruch -> Rollback, naechster Lauf traegt nach)
    t_meta = time.time()
    c.connection.autocommit(False)
    c.connection.begin()
    c.execute(f"SELECT COALESCE(MAX(last_oid), -1) + 1, COALESCE(MAX(chunk_no), -1) + 1 FROM {PREFIX}import")
    oid, chunk_no = c.fetchone()
    new_seq_h, new_acc_h = [], []
    for s in per_file:
        c.execute(f"INSERT INTO {PREFIX}release (quelle, datei, nr_datum, nr_sequenzen, nr_residues, dmnd_datei, "
                  "dmnd_hash, dmnd_sequenzen, meta_importiert, aktiv) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW(),0)",
                  (s["quelle"], s["datei"], s["datum"], s["neu"], s["letters"], DMND,
                   state.get("dmnd_hash"), state.get("dmnd_sequences")))
        rel = c.lastrowid
        if s["neu"] == 0:
            continue
        for kind, table, cols, cs in (("seq", "seq", "(oid, len, n_acc, title)", "utf8mb4"),
                                      ("acc", "acc", "(oid, pos, acc, taxid)", "ascii"),
                                      ("ncbi", "seq_ncbi", "(oid, ncbi_datum)", "ascii")):
            tmp = s["base"] + f".{kind}.load"
            with open(s["base"] + f".{kind}.tsv", encoding=cs if cs == "ascii" else "utf-8") as fi, \
                    open(tmp, "w", encoding=cs if cs == "ascii" else "utf-8") as fo:
                for line in fi:
                    o, rest = line.split("\t", 1)
                    fo.write(f"{oid + int(o)}\t{rest}")
                    if kind == "acc":
                        new_acc_h.append(h64(rest.split("\t")[1].encode()))
            c.execute(f"LOAD DATA LOCAL INFILE '{tmp}' INTO TABLE {PREFIX}{table} CHARACTER SET {cs} "
                      f"FIELDS TERMINATED BY '\\t' ESCAPED BY '' LINES TERMINATED BY '\\n' {cols}")
        c.execute(f"INSERT INTO {PREFIX}import (chunk_no, release_id, first_oid, last_oid, n_seq, n_acc, sekunden) "
                  "VALUES (%s,%s,%s,%s,%s,%s,0)", (chunk_no, rel, oid, oid + s["neu"] - 1, s["neu"], s["neu"]))
        chunk_no += 1
        oid += s["neu"]
    c.connection.commit()
    c.connection.autocommit(True)
    log(f"step metadata transaction: {time.time() - t_meta:.0f}s")
    new_seq_h = list(seen.keys())

    del known
    t_merge = time.time()
    merge_in_datei(SEQ_CACHE, new_seq_h)
    if os.path.exists(ACC_CACHE) and new_acc_h:
        merge_in_datei(ACC_CACHE, new_acc_h)
    log(f"step hash cache merge: {time.time() - t_merge:.0f}s")
    shutil.rmtree(WORK)
    body = "\n".join(f"{s['quelle']} {s['datei']} ({s['datum']}): {s['records']} Proteine, {s['neu']} neu, "
                     f"{s['dup_nr']} schon in nr, {s['dup_lauf']} doppelt" for s in per_file)
    body += f"\n\nnr_full.dmnd: {state.get('dmnd_sequences')} Sequenzen, Hash {state.get('dmnd_hash')}\n"
    log(f"=== DELTA FERTIG: {total_new} neue Sequenzen ===")
    mail(f"nr-Tagesdelta: {total_new} neue Sequenzen", body)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FEHLER: {type(e).__name__}: {e}")
        try:
            mail("nr-Tagesdelta FEHLER", f"{type(e).__name__}: {e}\nLog: {LOG}")
        except Exception:
            pass
        sys.exit(1)
