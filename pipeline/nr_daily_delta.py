#!/usr/bin/env python3
"""Taegliches Delta fuer die nr-Teildatenbanken (nr_parts, seit 2026-11 statt nr_full.dmnd) und blast_* aus
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
Alle offenen Tagesdateien seit dem letzten nr-Release werden in EINEM Lauf verarbeitet.

Parts: every new sequence (one accession, one taxid) goes to the part holding its taxon group (groups of
nr_acc_scan.py, routing table blast_part_group). Per part one makedb --append (with SEG masking, the sequences
are new), then <part>.oids.npy and blast_part are extended. The metadata transaction writes the new size of the
whole nr (dmnd_sequenzen, dmnd_letters, dmnd_hash = nr_parts.combined_hash) into the new blast_release rows and
the state of every part into blast_part_release. Restart-safe: a part whose .dmnd already has the new records
only gets its OID list written.
"""
import os, re, sys, json, gzip, time, fcntl, shutil, hashlib, subprocess, urllib.request, smtplib
from email.mime.text import MIMEText
import numpy as np

import blast_db, pymysql

E = os.environ.get
PREFIX = E("BLAST_PREFIX", "blast_")


def find_diamond():
    """DIAMOND with makedb --append: $DIAMOND_APPEND_BIN, else the master build the parts were made with,
    else PATH incl. ~/.local/bin (cron has a minimal PATH), else the old build directory."""
    if E("DIAMOND_APPEND_BIN"):
        return E("DIAMOND_APPEND_BIN")
    master = os.path.expanduser("~/build/diamond-master-61bbbe491bed/build/diamond")
    if os.access(master, os.X_OK):
        return master
    path = os.pathsep.join([os.path.expanduser("~/.local/bin"), E("PATH", "/usr/local/bin:/usr/bin:/bin")])
    for cand in (shutil.which("diamond", path=path), os.path.expanduser("~/src/diamond/build/diamond")):
        if cand and os.access(cand, os.X_OK):
            return cand
    return os.path.expanduser("~/.local/bin/diamond")


DIAMOND = find_diamond()
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


NET_WAITS = [30, 120, 300, 600, 1200]   # s between attempts, ~37 min total: NCBI TLS hangs pass


def _retry(what, fn):
    """Run fn() and retry transient network errors (TLS/connect timeout, reset, 5xx)."""
    import socket, http.client, urllib.error
    for i, wait in enumerate(NET_WAITS + [None]):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            if e.code < 500 or wait is None:
                raise RuntimeError(f"{what}: HTTP {e.code}") from e
            err = e
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError,
                http.client.HTTPException) as e:
            if wait is None:
                raise RuntimeError(f"{what}: {type(e).__name__}: {e} "
                                   f"(after {len(NET_WAITS) + 1} attempts)") from e
            err = e
        log(f"network retry {i + 1}/{len(NET_WAITS)} in {wait}s: {what}: {type(err).__name__}: {err}")
        time.sleep(wait)


def fetch(url, method=None):
    """Body (GET) or headers (HEAD) of url, with timeout and retries."""
    def go():
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.headers if method == "HEAD" else r.read()
    return _retry(url, go)


def download(url, dest):
    """url -> dest.part -> dest; 60 s socket timeout per read, length check, retries."""
    def go():
        with urllib.request.urlopen(url, timeout=60) as r, open(dest + ".part", "wb") as f:
            shutil.copyfileobj(r, f, 1 << 22)
            want = r.headers.get("Content-Length")
        got = os.path.getsize(dest + ".part")
        if want is not None and got != int(want):
            raise ConnectionError(f"short read {got}/{want} bytes")
    _retry(url, go)
    os.replace(dest + ".part", dest)


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
        html = fetch(base).decode()
        for name, d in re.findall(rf'href="({pat})">[^<]*</a>\s+(\d{{4}}-\d{{2}}-\d{{2}})', html):
            out.append((q, base + name, name, d))
    for q, url in SINGLE_SOURCES.items():
        lm = fetch(url, "HEAD")["Last-Modified"]
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
    want = fetch(TAX_URL + ".md5").decode().split()[0]
    download(TAX_URL, tgz + ".part")
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


OUT_EXT = (".fa", ".map", ".seq.tsv", ".acc.tsv", ".ncbi.tsv")


def fa_hashes(fa):
    """Sequence hashes of a delta .fa, to rebuild the in-run duplicate set on restart."""
    seq = []
    for line in open(fa):
        if line.startswith(">"):
            if seq:
                yield h64("".join(seq).encode())
            seq = []
        else:
            seq.append(line.strip())
    if seq:
        yield h64("".join(seq).encode())


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


def check_diamond():
    """Fail before any download if the binary is missing or lacks the --append patch."""
    if not os.access(DIAMOND, os.X_OK):
        raise RuntimeError(f"diamond binary not found/executable: {DIAMOND} "
                           "(build v2.2.8 + ~/src/blastDiamond/diamond/*.patch, install to ~/.local/bin)")
    try:
        r = subprocess.run([DIAMOND, "makedb"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"diamond {DIAMOND} not runnable: {e}")
    if "--append" not in r.stdout:
        raise RuntimeError(f"diamond {DIAMOND} has no --append (unpatched build?)")


def save_state(state, state_file):
    """Write the state atomically (tmp + rename), so a failing dump never leaves a truncated file."""
    with open(state_file + ".tmp", "w") as f:
        json.dump(state, f)
    os.replace(state_file + ".tmp", state_file)


def append_to_parts(c, per_file, state, state_file):
    """Distribute the new records (in OID order over all delta files) to the parts by taxon group and append them
    there. The OIDs continue at the end of the whole nr (blast_release dmnd_sequenzen = MAX(last_oid) + 1 in
    blast_import)."""
    import nr_parts
    from nr_acc_scan import group_table_from_parent, nodes_parent, NAMES, NONE
    c.execute(f"SELECT part, dmnd FROM {PREFIX}part WHERE part REGEXP %s", (nr_parts.PART_RE,))
    parts = {p: (d,) for p, d in c.fetchall()}
    gp = nr_parts.group_parts(c)
    missing = [g for g in NAMES if gp.get(g) not in parts]
    if missing:
        raise RuntimeError(f"taxon groups in no built part ({PREFIX}part_group): {missing}")
    part_of_group = {NAMES.index(g): gp[g] for g in NAMES}
    if "nr_before" not in state:                  # size before this run; the DB keeps it until the metadata commit
        _, n, l, _ = nr_parts.nr_size(c)
        state["nr_before"] = {"n_seq": n, "letters": l}
        save_state(state, state_file)
    before = state["nr_before"]
    c.execute(f"SELECT COALESCE(MAX(last_oid), -1) + 1 FROM {PREFIX}import")
    oid0 = int(c.fetchone()[0])                   # MariaDB returns MAX()+1 as Decimal, not JSON serializable
    if oid0 != int(before["n_seq"]):
        raise RuntimeError(f"OID end {oid0} in {PREFIX}import != size of the whole nr {before['n_seq']} (blast_release)")
    table = group_table_from_parent(nodes_parent(f"{TAXDIR}/nodes.dmp"))   # just refreshed; blast_taxon lags a day

    # split: per part a FASTA, a taxonmap and the OIDs, all in OID order
    out, oids, j = {}, {}, 0
    for s in per_file:
        taxa = [int(l.split("\t")[1]) for l in open(s["base"] + ".map")]
        k = -1
        for line in open(s["base"] + ".fa"):
            if line.startswith(">"):
                k += 1
                t = taxa[k]
                p = part_of_group[table[t] if 0 < t < len(table) else NONE]
                if p not in out:
                    out[p] = (open(os.path.join(WORK, f"part_{p}.fa"), "w"), open(os.path.join(WORK, f"part_{p}.map"), "w"))
                    out[p][1].write("accession.version\ttaxid\n")
                    oids[p] = []
                oids[p].append(oid0 + j)
                out[p][1].write(f"{line[1:].split()[0]}\t{t}\n")
                j += 1
            out[p][0].write(line)
        if k + 1 != s["neu"]:
            raise RuntimeError(f"{s['base']}: {k + 1} FASTA records, {s['neu']} expected")
    for fa, fm in out.values():
        fa.close(), fm.close()

    from dmnd_getseq import Dmnd
    done = state.setdefault("parts_new", {})
    for p in sorted(oids):
        dmnd, new = parts[p][0], np.array(oids[p], dtype=np.uint64)
        npy = dmnd[:-len(".dmnd")] + ".oids.npy"
        old = np.load(npy, mmap_mode="r")
        if len(old) and old[-1] >= new[0]:
            if len(old) >= len(new) and old[-len(new)] == new[0]:
                log(f"{p}: OID list already extended (restart)")
                done[p] = len(new)
                continue
            raise RuntimeError(f"{p}: OID list ends at {old[-1]}, new OIDs start at {new[0]}")
        n_db = Dmnd(dmnd).n_seq
        if n_db == len(old):
            cmd = (f"{DIAMOND} makedb --append --db {dmnd} --in {WORK}/part_{p}.fa --taxonmap {WORK}/part_{p}.map "
                   f"--taxonnodes {TAXDIR}/nodes.dmp --taxonnames {TAXDIR}/names.dmp --threads 20 --no-parse-seqids")
            log(f"=== {p}: makedb --append, {len(new)} neue Sequenzen ===")
            r = subprocess.run(["bash", "-o", "pipefail", "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
            with open(LOG, "a") as f:
                f.write(r.stdout[-3000:])
            if r.returncode != 0:
                raise RuntimeError(f"{p}: diamond makedb --append fehlgeschlagen (Teil per Rollback unveraendert)")
            h = [l.split()[-1] for l in r.stdout.splitlines() if "Database hash" in l]
            if h:
                c.execute(f"UPDATE {PREFIX}part SET dmnd_hash=%s WHERE part=%s", (h[0], p))
        elif n_db != len(old) + len(new):
            raise RuntimeError(f"{p}: {n_db} records in the .dmnd, {len(old)} OIDs, {len(new)} new")
        d = Dmnd(dmnd)
        tmp = npy + ".tmp.npy"
        np.save(tmp, np.concatenate([np.asarray(old), new]))
        os.replace(tmp, npy)
        c.execute(f"UPDATE {PREFIX}part SET n_seq=%s, n_acc=n_acc+%s, letters=%s, built=NOW() WHERE part=%s",
                  (d.n_seq, len(new), d.letters, p))
        done[p] = len(new)
        save_state(state, state_file)

    # size and identifier of the whole nr; written to blast_release in the metadata transaction
    state["dmnd_letters"] = int(before["letters"]) + sum(s["letters"] for s in per_file)
    state["dmnd_hash"] = nr_parts.combined_hash(c)
    state["dmnd_sequences"] = oid0 + j
    state["dmnd_done"] = True
    save_state(state, state_file)


def main():
    os.makedirs(WORK, exist_ok=True)
    check_diamond()
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
    c.execute(f"SELECT part, dmnd FROM {PREFIX}part WHERE part REGEXP '^p[0-9]+_'")
    for _, d in c.fetchall():
        if os.path.exists(d + ".append_backup"):
            raise RuntimeError(f"{d}.append_backup existiert: frueherer Append unvollstaendig")

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
    if state.get("key", key) != key and state.get("parts_new"):
        # the parts already hold the sequences of the unfinished run (append done, metadata commit missing):
        # finish exactly that run first; files that appeared in the meantime wait for the next invocation
        old_key = state["key"].split("|")
        pending = [x for x in pending if f"{x[0]}:{x[2]}:{x[3]}" in old_key]
        key = "|".join(f"{q}:{n}:{d}" for q, _, n, d in pending)
        if key != state["key"]:
            raise RuntimeError(f"unfinished run {state['key']} does not match the pending files {key}")
        log(f"unfinished run with appended parts: finishing its {len(pending)} files first")
    if state.get("key", key) != key:
        state = alter_lauf_abschliessen(state, done_db)
    state["key"] = key

    known = np.load(SEQ_CACHE, mmap_mode="r")
    seen, per_file = {}, []
    # restart: files parsed by an earlier, failed run are reused without download/parse, as long as
    # the files before them are the same (in-run duplicate removal depends on that order)
    parsed_file = os.path.join(WORK, "parsed.json")
    parsed = json.load(open(parsed_file)) if os.path.exists(parsed_file) else {}
    tags, reused_fa = [], []
    for q, src, name, d in pending:
        tag = f"{q}_{d}_{name}"
        base = os.path.join(WORK, tag)
        path = base + ".gz"
        p = parsed.get(tag)
        if p and p["prefix"] == tags and all(os.path.exists(base + x) for x in OUT_EXT):
            per_file.append(p["st"])
            tags.append(tag)
            reused_fa.append(base + ".fa")
            log(f"{q} {name} ({d}): already parsed by an earlier run, {p['st']['neu']} new (restart, no download)")
            continue
        for fa in reused_fa:
            seen.update(dict.fromkeys(fa_hashes(fa)))
        reused_fa = []
        t_dl = time.time()
        if not src.startswith("http"):
            shutil.copy(src, path)
        elif not os.path.exists(path):
            download(src, path)
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
        parsed[tag] = {"prefix": list(tags), "st": st}
        tags.append(tag)
        with open(parsed_file + ".tmp", "w") as f:
            json.dump(parsed, f)
        os.replace(parsed_file + ".tmp", parsed_file)
        log(f"{q} {name} ({d}): {len(recs)} Proteine, {n_new} neu, {n_dup_nr} schon in nr, "
            f"{n_dup_run} doppelt im Delta ({time.time() - t0:.0f}s, download {dl_s:.0f}s)")

    for fa in reused_fa:                          # hash cache merge below needs all new hashes
        seen.update(dict.fromkeys(fa_hashes(fa)))
    total_new = sum(s["neu"] for s in per_file)
    if total_new and not state.get("dmnd_done"):
        t_tax = time.time()
        refresh_taxdump()
        log(f"step taxdump check/refresh: {time.time() - t_tax:.1f}s")
        t_mk = time.time()
        append_to_parts(c, per_file, state, state_file)
        log(f"Append fertig: {state['dmnd_sequences']} Sequenzen, Hash {state['dmnd_hash']} "
            f"(step makedb --append: {time.time() - t_mk:.0f}s)")

    # Metadaten: je Tagesdatei eine Release-Zeile und ein OID-Bereich - alles in EINER Transaktion,
    # damit MariaDB nie halb zum dmnd passt (Abbruch -> Rollback, naechster Lauf traegt nach)
    t_meta = time.time()
    c.connection.autocommit(False)
    c.connection.begin()
    c.execute(f"SELECT COALESCE(MAX(last_oid), -1) + 1, COALESCE(MAX(chunk_no), -1) + 1 FROM {PREFIX}import")
    oid, chunk_no = map(int, c.fetchone())   # MariaDB returns MAX()+1 as Decimal
    new_seq_h, new_acc_h = [], []
    for s in per_file:
        c.execute(f"INSERT INTO {PREFIX}release (quelle, datei, nr_datum, nr_sequenzen, nr_residues, dmnd_datei, "
                  "dmnd_hash, dmnd_sequenzen, dmnd_letters, meta_importiert, aktiv) "
                  "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),0)",
                  (s["quelle"], s["datei"], s["datum"], s["neu"], s["letters"], "nr parts",
                   state.get("dmnd_hash"), state.get("dmnd_sequences"), state.get("dmnd_letters")))
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
    import nr_parts
    nr_parts.part_release_rows(c, rel)               # state of every part after this run
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
    body += f"\n\nnr (parts): {state.get('dmnd_sequences')} Sequenzen, Hash {state.get('dmnd_hash')}\n"
    body += "".join(f"  {p}: +{n}\n" for p, n in sorted(state.get("parts_new", {}).items()))
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
