#!/usr/bin/env python3
"""Naechtlicher nr-Update fuer nr_full.dmnd und die blast_*-Tabellen in wagodb.

1. NCBI nr-prot-metadata.json mit dem neuesten Eintrag in blast_release vergleichen;
   gleiches Release -> sofort Ende.
2. Neues Release: Volume fuer Volume laden (md5-geprueft), entpacken, Accessions gegen
   ein sortiertes 64-bit-Hash-Array aller bekannten Accessions pruefen, Volume loeschen.
   Neu ist eine Sequenz, wenn KEINE ihrer Accessions bekannt ist.
3. Neue Sequenzen per gepatchtem `diamond makedb --append` anhaengen (Rollback im Patch),
   danach Metadaten, Release-Zeile und Hash-Array fortschreiben, Mail an MAIL_TO.

Fortsetzbar: fertige Volumes haben eine .done-Datei im Arbeitsordner, ein erfolgter
DIAMOND-Append eine state-Datei, damit er nie doppelt laeuft.
Grenzen: neue Accessions zu schon vorhandenen Sequenzen und von NCBI zurueckgezogene
Sequenzen werden erst bei einem Vollneubau beruecksichtigt (nur gezaehlt).
"""
import os, sys, json, time, fcntl, hashlib, shutil, subprocess, urllib.request, smtplib
from email.mime.text import MIMEText
import numpy as np

import blast_db, pymysql

E = os.environ.get
PREFIX = E("BLAST_PREFIX", "blast_")
DMND = E("DMND", "/home/gh/diamond/nr_full.dmnd")
DIAMOND = E("DIAMOND_APPEND_BIN", os.path.expanduser("~/src/diamond/build/diamond"))
BLASTDBCMD = os.path.expanduser("~/iver_sim/mamba/envs/blast/bin/blastdbcmd")
FILTER = os.path.expanduser("~/python/filter_nonempty.py")
TAXDIR = E("TAXDIR", "/mnt/archive/taxdump")
WORK = E("WORK", "/home/gh/nr_update")
CACHE = E("ACC_CACHE", "/mnt/archive/nr_acc_hashes.npy")
SEQ_CACHE = E("SEQ_CACHE", "/mnt/archive/nr_seq_hashes.npy")   # Sequenz-Hashes (nr + Tagesdeltas)
LOCK = E("NR_LOCK", "/home/gh/.nr_dmnd.lock")                  # gemeinsam mit nr_daily_delta.py
LOG = E("APPEND_LOG", os.path.expanduser("~/python/diamond_append.log"))
META_URL = "https://ftp.ncbi.nlm.nih.gov/blast/db/nr-prot-metadata.json"
TAX_URL = "https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/"
MAIL_TO = E("MAIL_TO", "")               # leer = keine Mail
SMTP_HOST = E("SMTP_HOST", "localhost")
MAIL_FROM = E("MAIL_FROM", "nr-update@localhost")
MAIL_EHLO = E("MAIL_EHLO", "localhost")

# Testmodus: lokale Volumes statt Download, begrenzte OIDs, eigenes Metadaten-JSON
TEST_DIR = E("TEST_VOLUME_DIR")          # Volumes liegen schon hier, werden nie geloescht
TEST_VOLUMES = E("TEST_VOLUMES")         # z.B. "nr.000"
TEST_MAX_OIDS = int(E("TEST_MAX_OIDS", "0"))
TEST_META = E("TEST_META_JSON")          # Pfad zu Ersatz-JSON
TEST_TAXONMAP = E("TEST_TAXONMAP")       # kleine Mapping-Datei statt FULL.gz
NO_MAIL = E("NO_MAIL") == "1"


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


def db():
    return pymysql.connect(**blast_db.cfg(), autocommit=True, local_infile=True)


def acc_hash(acc):
    return int.from_bytes(hashlib.blake2b(acc.encode(), digest_size=8).digest(), "little")


def fetch(url, dest):
    tmp = dest + ".part"
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 64 * 1024 * 1024)
    os.replace(tmp, dest)


def md5_ok(path, md5_url):
    want = urllib.request.urlopen(md5_url, timeout=60).read().decode().split()[0]
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(64 * 1024 * 1024), b""):
            h.update(b)
    return h.hexdigest() == want


def fetch_verified(url, dest, tries=3):
    for i in range(tries):
        fetch(url, dest)
        if md5_ok(dest, url + ".md5"):
            return
        log(f"md5-Fehler bei {url}, Versuch {i + 1}/{tries}")
    raise RuntimeError(f"md5 nach {tries} Versuchen falsch: {url}")


# ---------- bekannte Accessions ----------

def build_cache(c):
    c.execute(f"SELECT SUM(n_acc) FROM {PREFIX}import")
    n = int(c.fetchone()[0] or 0)
    log(f"Baue Accession-Hash-Array aus {PREFIX}acc ({n} Eintraege)")
    arr = np.empty(n, dtype=np.uint64)
    cur = pymysql.connect(**blast_db.cfg(), cursorclass=pymysql.cursors.SSCursor).cursor()
    cur.execute(f"SELECT acc FROM {PREFIX}acc")
    i = 0
    while True:
        rows = cur.fetchmany(1_000_000)
        if not rows:
            break
        arr[i:i + len(rows)] = [acc_hash(r[0]) for r in rows]
        i += len(rows)
    if i != n:
        raise RuntimeError(f"{PREFIX}acc hat {i} Zeilen, {PREFIX}import sagt {n}")
    arr.sort()
    np.save(CACHE + ".tmp.npy", arr)
    os.replace(CACHE + ".tmp.npy", CACHE)
    log("Hash-Array gespeichert")


def seq_hash(seq):
    """wie nr_seqhash_build.py / nr_daily_delta.py: blake2b-64 der Grossbuchstaben-Sequenz"""
    return int.from_bytes(hashlib.blake2b(seq.upper(), digest_size=8).digest(), "little")


def is_known(known, hashes):
    h = np.fromiter(hashes, dtype=np.uint64)
    idx = np.searchsorted(known, h)
    idx[idx == len(known)] = 0
    return known[idx] == h


# ---------- ein Volume ----------

def volume_pass(vol_path, vname, known, known_seq):
    """Schreibt vol_<name>.fa/.seq.tsv/.acc.tsv mit den neuen Sequenzen dieses Volumes."""
    env = {**os.environ, "BLASTDB": os.path.dirname(vol_path)}
    meta = subprocess.Popen([BLASTDBCMD, "-db", vol_path, "-entry", "all",
                             "-outfmt", "%o\t%a\t%T\t%l\t%t"], stdout=subprocess.PIPE, env=env)
    groups, cur, n_oids = [], None, 0
    for raw in meta.stdout:
        p = raw.decode("utf-8", "replace").rstrip("\n").split("\t", 4)
        p += [""] * (5 - len(p))
        oid = int(p[0])
        if TEST_MAX_OIDS and oid >= TEST_MAX_OIDS:
            meta.kill()
            break
        if cur is None or cur[0] != oid:
            cur = [oid, int(p[3] or 0), p[4], []]
            groups.append(cur)
        cur[3].append((p[1], int(p[2]) if p[2].isdigit() else 0))
    rc = meta.wait()
    if rc not in (0, -9) or (rc == -9 and not TEST_MAX_OIDS):
        raise RuntimeError(f"blastdbcmd Metadaten {vname} exit={rc}")
    n_oids = len(groups)

    flags = is_known(known, (acc_hash(a) for g in groups for a, _ in g[3]))
    new_oids, k, extra_acc = set(), 0, 0
    for g in groups:
        f = flags[k:k + len(g[3])]
        k += len(g[3])
        if not f.any():
            new_oids.add(g[0])
        elif not f.all():
            extra_acc += int((~f).sum())

    base = os.path.join(WORK, f"vol_{vname}")
    # FASTA der Kandidaten; Sequenzen, die schon per Tagesdelta angehaengt wurden, fallen raus
    fasta = subprocess.Popen([BLASTDBCMD, "-db", vol_path, "-entry", "all"], stdout=subprocess.PIPE, env=env)
    oid_list = [g[0] for g in groups]
    seq_hashes, dup_delta = [], 0
    with open(base + ".fa", "wb") as out:
        idx, rec = -1, None

        def flush(rec):
            nonlocal dup_delta
            if rec is None:
                return
            h = seq_hash(b"".join(l.strip() for l in rec[2:]))
            if len(known_seq) and is_known(known_seq, [h])[0]:
                new_oids.discard(rec[0])
                dup_delta += 1
                return
            seq_hashes.append(h)
            out.writelines(rec[1:])

        for line in fasta.stdout:
            if line.startswith(b">"):
                flush(rec)
                rec = None
                idx += 1
                if idx >= len(oid_list):
                    fasta.kill()
                    break
                if oid_list[idx] in new_oids:
                    rec = [oid_list[idx], line]
            elif rec is not None:
                rec.append(line)
        else:
            flush(rec)
    fasta.wait()
    np.save(base + ".seqh.npy", np.array(seq_hashes, dtype=np.uint64))

    with open(base + ".seq.tsv", "w", encoding="utf-8") as fs, open(base + ".acc.tsv", "w", encoding="ascii", errors="replace") as fa:
        ordinal = 0
        for g in groups:
            if g[0] in new_oids:
                title = g[2].replace("\t", " ")
                fs.write(f"{ordinal}\t{g[1]}\t{len(g[3])}\t{title}\n")
                for pos, (a, t) in enumerate(g[3]):
                    fa.write(f"{ordinal}\t{pos}\t{a}\t{t}\n")
                ordinal += 1
    stats = {"oids": n_oids, "neu": len(new_oids), "neue_acc_an_alten_seq": extra_acc, "schon_per_delta": dup_delta}
    with open(base + ".done", "w") as f:
        json.dump(stats, f)
    return stats


# ---------- Hauptablauf ----------

def main():
    os.makedirs(WORK, exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("Anderer nr-Update laeuft, Ende")
        return

    c = db().cursor()
    c.execute(f"SELECT release_id, nr_datum, nr_sequenzen, dmnd_hash, meta_importiert FROM {PREFIX}release "
              "WHERE quelle='nr' ORDER BY nr_datum DESC LIMIT 1")
    last = c.fetchone()
    if not last or last[3] is None or last[4] is None:
        log("Erstaufbau (dmnd_hash/meta_importiert) noch nicht abgeschlossen, Ende")
        return
    if os.path.exists(DMND + ".append_backup"):
        raise RuntimeError(f"{DMND}.append_backup existiert: frueherer Append unvollstaendig")

    meta = json.load(open(TEST_META)) if TEST_META else json.load(urllib.request.urlopen(META_URL, timeout=60))
    ncbi_date = meta["last-updated"].replace("T", " ")[:19]
    if (ncbi_date[:10] <= str(last[1])[:10] and int(meta["number-of-sequences"]) == int(last[2])):
        log(f"Kein neues Release (NCBI {ncbi_date}, lokal {last[1]}), Ende")
        return
    log(f"=== Neues nr-Release {ncbi_date}: {meta['number-of-sequences']} Sequenzen (lokal {last[1]}: {last[2]}) ===")
    state_file = os.path.join(WORK, "state.json")
    state = json.load(open(state_file)) if os.path.exists(state_file) else {"release": ncbi_date}
    if state["release"] != ncbi_date:
        raise RuntimeError(f"Arbeitsordner {WORK} gehoert zu Release {state['release']}, bitte pruefen")

    # Taxonomie des neuen Releases
    if TEST_TAXONMAP:
        taxonmap = TEST_TAXONMAP
    else:
        taxonmap = os.path.join(TAXDIR, "prot.accession2taxid.FULL.gz")
        if not state.get("tax_done"):
            log("Lade Taxonomie-Dateien des neuen Releases")
            fetch_verified(TAX_URL + "accession2taxid/prot.accession2taxid.FULL.gz", taxonmap)
            fetch_verified(TAX_URL + "taxdump.tar.gz", os.path.join(TAXDIR, "taxdump.tar.gz"))
            subprocess.run(["tar", "-xzf", "taxdump.tar.gz", "nodes.dmp", "names.dmp"], cwd=TAXDIR, check=True)
            state["tax_done"] = True
            json.dump(state, open(state_file, "w"))

    if not os.path.exists(CACHE):
        build_cache(c)
    known = np.load(CACHE, mmap_mode="r")
    known_seq = np.load(SEQ_CACHE, mmap_mode="r") if os.path.exists(SEQ_CACHE) else np.zeros(0, dtype=np.uint64)

    volumes = TEST_VOLUMES.split(",") if TEST_VOLUMES else \
        [os.path.basename(u).replace(".tar.gz", "") for u in meta["files"]]
    totals = {"oids": 0, "neu": 0, "neue_acc_an_alten_seq": 0, "schon_per_delta": 0}
    for vname in volumes:
        done = os.path.join(WORK, f"vol_{vname}.done")
        if os.path.exists(done):
            st = json.load(open(done))
        else:
            t0 = time.time()
            if TEST_DIR:
                st = volume_pass(os.path.join(TEST_DIR, vname), vname, known, known_seq)
            else:
                vdir = os.path.join(WORK, "vol")
                shutil.rmtree(vdir, ignore_errors=True)
                os.makedirs(vdir)
                tgz = os.path.join(vdir, vname + ".tar.gz")
                fetch_verified(f"https://ftp.ncbi.nlm.nih.gov/blast/db/{vname}.tar.gz", tgz)
                subprocess.run(["tar", "-xzf", tgz], cwd=vdir, check=True)
                os.remove(tgz)
                st = volume_pass(os.path.join(vdir, vname), vname, known, known_seq)
                shutil.rmtree(vdir)
            log(f"{vname}: {st['oids']} Sequenzen, {st['neu']} neu, "
                f"{st['neue_acc_an_alten_seq']} neue Accessions an vorhandenen Sequenzen, "
                f"{st['schon_per_delta']} schon per Tagesdelta ({time.time() - t0:.0f}s)")
        for k in totals:
            totals[k] += st.get(k, 0)

    log(f"Summe: {totals['neu']} neue Sequenzen in {totals['oids']}")
    parts = [os.path.join(WORK, f"vol_{v}") for v in volumes]

    # DIAMOND-Append (genau einmal)
    if not state.get("dmnd_done") and totals["neu"] > 0:
        log("=== diamond makedb --append ===")
        cmd = f"cat {' '.join(p + '.fa' for p in parts)} | python3 {FILTER} | {DIAMOND} makedb --append " \
              f"--db {DMND} --taxonmap {taxonmap} --taxonnodes {TAXDIR}/nodes.dmp --taxonnames {TAXDIR}/names.dmp --threads 20"
        r = subprocess.run(["bash", "-o", "pipefail", "-c", cmd], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
        with open(LOG, "a") as f:
            f.write(r.stdout[-4000:])
        if r.returncode != 0:
            raise RuntimeError("diamond makedb --append fehlgeschlagen (DB per Rollback unveraendert)")
        info = subprocess.run([DIAMOND, "dbinfo", "--db", DMND, "--quiet"], capture_output=True, text=True).stdout
        state["dmnd_sequences"] = int([l for l in info.splitlines() if "Sequences" in l][0].split()[-1])
        state["dmnd_hash"] = [l for l in r.stdout.splitlines() if "Database hash" in l][0].split()[-1]
        state["dmnd_done"] = True
        json.dump(state, open(state_file, "w"))
        log(f"Append fertig: {state['dmnd_sequences']} Sequenzen, Hash {state['dmnd_hash']}")

    # Metadaten - eine Transaktion: MariaDB passt immer vollstaendig zum dmnd oder bleibt beim alten Stand
    c.connection.autocommit(False)
    c.connection.begin()
    c.execute(f"INSERT IGNORE INTO {PREFIX}release (quelle, nr_datum, nr_sequenzen, nr_residues, taxdump_geladen, "
              "acc2taxid_geladen, dmnd_datei, dmnd_hash, dmnd_sequenzen, aktiv) VALUES ('nr',%s,%s,%s,NOW(),NOW(),%s,%s,%s,0)",
              (ncbi_date, meta["number-of-sequences"], meta["number-of-letters"], DMND,
               state.get("dmnd_hash", last[3]), state.get("dmnd_sequences")))
    c.execute(f"SELECT release_id FROM {PREFIX}release WHERE quelle='nr' AND nr_datum=%s", (ncbi_date,))
    rel = c.fetchone()[0]
    c.execute(f"SELECT COALESCE(MAX(last_oid), -1) + 1, COALESCE(MAX(chunk_no), -1) + 1 FROM {PREFIX}import WHERE release_id<>%s", (rel,))
    base_oid, chunk_no = c.fetchone()
    c.execute(f"DELETE FROM {PREFIX}seq WHERE oid >= %s", (base_oid,))
    c.execute(f"DELETE FROM {PREFIX}acc WHERE oid >= %s", (base_oid,))
    c.execute(f"DELETE FROM {PREFIX}import WHERE release_id=%s", (rel,))

    oid, t0 = base_oid, time.time()
    seq_tmp, acc_tmp = os.path.join(WORK, "load_seq.tsv"), os.path.join(WORK, "load_acc.tsv")
    new_hashes = []
    for p in parts:
        with open(p + ".seq.tsv", encoding="utf-8") as fi, open(seq_tmp, "w", encoding="utf-8") as fo:
            n_seq = 0
            for line in fi:
                o, rest = line.split("\t", 1)
                fo.write(f"{oid + int(o)}\t{rest}")
                n_seq += 1
        with open(p + ".acc.tsv", encoding="ascii") as fi, open(acc_tmp, "w", encoding="ascii") as fo:
            n_acc = 0
            for line in fi:
                o, pos, a, t = line.rstrip("\n").split("\t")
                fo.write(f"{oid + int(o)}\t{pos}\t{a}\t{t}\n")
                new_hashes.append(acc_hash(a))
                n_acc += 1
        if n_seq == 0:
            continue
        for tbl, path, cols, cs in ((f"{PREFIX}seq", seq_tmp, "(oid, len, n_acc, title)", "utf8mb4"),
                                    (f"{PREFIX}acc", acc_tmp, "(oid, pos, acc, taxid)", "ascii")):
            c.execute(f"LOAD DATA LOCAL INFILE '{path}' INTO TABLE {tbl} CHARACTER SET {cs} "
                      f"FIELDS TERMINATED BY '\\t' ESCAPED BY '' LINES TERMINATED BY '\\n' {cols}")
        c.execute(f"INSERT INTO {PREFIX}import (chunk_no, release_id, first_oid, last_oid, n_seq, n_acc, sekunden) "
                  "VALUES (%s,%s,%s,%s,%s,%s,%s)", (chunk_no, rel, oid, oid + n_seq - 1, n_seq, n_acc, round(time.time() - t0, 2)))
        chunk_no += 1
        oid += n_seq
    c.execute(f"UPDATE {PREFIX}release SET aktiv=(release_id=%s) WHERE quelle='nr'", (rel,))
    c.execute(f"UPDATE {PREFIX}release SET meta_importiert=NOW() WHERE release_id=%s", (rel,))
    c.connection.commit()
    c.connection.autocommit(True)
    log(f"Metadaten: {oid - base_oid} Sequenzen ab OID {base_oid} als Release {rel} eingetragen")

    # Hash-Array fortschreiben
    if new_hashes:
        merged = np.union1d(np.asarray(known), np.array(new_hashes, dtype=np.uint64))
        del known
        np.save(CACHE + ".tmp.npy", merged)
        os.replace(CACHE + ".tmp.npy", CACHE)
        log(f"Hash-Array: {len(merged)} Accessions")
    seq_parts = [np.load(p + ".seqh.npy") for p in parts if os.path.exists(p + ".seqh.npy")]
    if seq_parts and os.path.exists(SEQ_CACHE):
        merged = np.union1d(np.load(SEQ_CACHE, mmap_mode="r"), np.concatenate(seq_parts))
        np.save(SEQ_CACHE + ".tmp.npy", merged)
        os.replace(SEQ_CACHE + ".tmp.npy", SEQ_CACHE)
        log(f"Sequenz-Hash-Array: {len(merged)} Sequenzen")

    body = (f"nr-Release {ncbi_date}\nneue Sequenzen: {totals['neu']}\n"
            f"neue Accessions an vorhandenen Sequenzen (nicht uebernommen): {totals['neue_acc_an_alten_seq']}\n"
            f"schon per Tagesdelta vorhanden (nicht erneut angehaengt): {totals['schon_per_delta']}\n"
            f"nr_full.dmnd: {state.get('dmnd_sequences')} Sequenzen, Hash {state.get('dmnd_hash')}\n")
    shutil.rmtree(WORK)
    log("=== APPEND FERTIG ===")
    mail(f"nr-Update {ncbi_date[:10]}: {totals['neu']} neue Sequenzen", body)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FEHLER: {type(e).__name__}: {e}")
        try:
            mail("nr-Update FEHLER", f"{type(e).__name__}: {e}\nLog: {LOG}")
        except Exception:
            pass
        sys.exit(1)
