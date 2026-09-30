#!/usr/bin/env python3
"""Sequenzen aus der DIAMOND-Datenbank holen -- Ersatz fuer blastdbcmd -entry.

Die .dmnd-Datei traegt hinter den Sequenzdaten eine Offset-Tabelle mit 16 Byte je
Satz. Ueber sie wird direkt an die Sequenz gesprungen: zwei Spruenge je Accession
statt eines Durchlaufs. Damit werden die BLAST-Baende in ~/blastdb nicht mehr
gebraucht, und die Residuen liegen nur noch einmal auf der Platte.

Kopf: Magic u64, build u32, db_version u32, sequences u64, letters u64,
pos_array_offset u64. DIAMOND schreibt in der Wirtsreihenfolge -- sein
big_endian_byteswap tut auf x86 nichts --, also klein-endian. Ein Satz an
Position pos:

    \\xff  <laenge Bytes Alphabet-Indizes>  \\xff  <Accession>\\0

Die Tabelle hat einen Eintrag mehr als Saetze da sind -- der letzte begrenzt den
letzten Satz. Leersequenzen weist makedb ab; ihre OIDs stehen in
blast_dmnd_luecke und verschieben die Satznummer gegen die OID.

  dmnd_getseq.py P0DTC2 P0DTD1             Accessions, FASTA nach stdout
  dmnd_getseq.py --roh P0DTC2              nur die Residuen, eine Zeile
  dmnd_getseq.py --range 319-541 P0DTC2    Teilbereich, 1-basiert, beide Enden inklusiv
  dmnd_getseq.py --oid 4711                nach OID statt nach Accession
  dmnd_getseq.py --pruefe 200              gegen blastdbcmd vergleichen, solange es die noch gibt
"""
import argparse, bisect, os, random, struct, subprocess, sys

DMND = os.environ.get("NR_DMND", "/home/gh/diamond/nr_full.dmnd")
BLASTDBCMD = os.path.expanduser("~/iver_sim/mamba/envs/blast/bin/blastdbcmd")
BLASTDB = os.path.expanduser("~/blastdb/nr")

ALPHABET = b"ARNDCQEGHILKMFPSTWYVBJZX*_"      # src/basic/value.h, Index = gespeicherter Wert
UEBER = bytes(ALPHABET[i] if i < len(ALPHABET) else ord("X") for i in range(256))
MAGIC = 0x24af8a415ee186d
KOPF = struct.Struct("<QIIQQQ")                # magic, build, db_version, sequences, letters, tabelle
SATZ = struct.Struct("<QII")                   # pos, laenge, Fuellfeld -- SeqInfo::SIZE = 16


class Dmnd:
    """Lesezugriff auf eine DIAMOND-Datenbank ueber ihre Offset-Tabelle."""

    def __init__(self, pfad=DMND):
        self.pfad = pfad
        self.f = open(pfad, "rb")
        magic, self.build, self.version, self.n_seq, self.letters, self.tabelle = \
            KOPF.unpack(self.f.read(KOPF.size))
        if magic != MAGIC:
            raise ValueError(f"{pfad}: keine DIAMOND-Datenbank (Magic {magic:#x})")
        if self.tabelle == 0:                  # makedb schreibt den Kopf erst am Ende neu
            raise ValueError(f"{pfad}: Offset-Tabelle fehlt -- der Bau laeuft noch")

    def satz(self, rec):
        """Ein Satz nach laufender Nummer: (Accession, Residuen)."""
        if not 0 <= rec < self.n_seq:
            raise IndexError(f"Satz {rec} ausserhalb 0..{self.n_seq - 1}")
        self.f.seek(self.tabelle + rec * SATZ.size)
        b = self.f.read(2 * SATZ.size)         # eigener Eintrag und der naechste: der begrenzt die ID
        pos, laenge, _ = SATZ.unpack_from(b, 0)
        ende, _, _ = SATZ.unpack_from(b, SATZ.size)
        self.f.seek(pos)
        roh = self.f.read(ende - pos)
        if roh[:1] != b"\xff" or roh[laenge + 1:laenge + 2] != b"\xff":
            raise ValueError(f"Satz {rec}: Begrenzer fehlt -- Offset-Tabelle passt nicht zur Datei")
        return (roh[laenge + 2:].split(b"\0", 1)[0].decode(),
                roh[1:laenge + 1].translate(UEBER).decode())

    def saetze(self, ab=0, roh=False):
        """Alle Saetze ab einer Nummer der Reihe nach, rein sequenziell gelesen.
        Fuer Durchlaeufe ueber die ganze Datenbank -- die Offset-Tabelle wird dabei
        nur fuer den ersten Sprung gebraucht. Liefert (Satznummer, ID, Residuen).
        Mit roh=True bleiben ID und Residuen Bytes: ueber die ganze nr spart das
        rund 480 GB Dekodierarbeit, und Vergleiche laufen ohnehin auf Bytes."""
        self.f.seek(self.tabelle + ab * SATZ.size)
        pos, _, _ = SATZ.unpack(self.f.read(SATZ.size))
        g = open(self.pfad, "rb", buffering=0)
        g.seek(pos)
        buf, i, n = b"", 0, ab
        while n < self.n_seq:
            if len(buf) - i < 1 << 20:                 # nachfuellen, bevor ein Satz ueber den Rand laeuft
                buf, i = buf[i:] + g.read(1 << 24), 0
            e = buf.index(b"\xff", i + 1)              # Ende der Residuen
            z = buf.index(b"\0", e + 1)                # Ende der ID
            kennung, seq = buf[e + 1:z], buf[i + 1:e].translate(UEBER)
            yield (n, kennung, seq) if roh else (n, kennung.decode(), seq.decode())
            i, n = z + 1, n + 1
        g.close()


def flicken(c, oid, seq):
    """Die von makedb weggemaskierten Abschnitte aus blast_mask zurueckschreiben.
    Ohne das kommen Bereiche niedriger Komplexitaet als X-Laeufe heraus (1,97 % der
    Residuen der nr, 14,5 % der Sequenzen betroffen)."""
    c.execute("SELECT start, orig FROM blast_mask WHERE oid = %s ORDER BY start", (oid,))
    st = c.fetchall()
    if not st:
        return seq
    teile, i = [], 0
    for start, orig in st:
        o = orig.decode() if isinstance(orig, bytes) else orig
        teile += [seq[i:start], o]
        i = start + len(o)
    teile.append(seq[i:])
    return "".join(teile)


def luecken(c):
    """OIDs, die makedb als Leersequenz abgewiesen hat. Ohne sie ist OID = Satznummer."""
    c.execute("SELECT oid FROM blast_dmnd_luecke ORDER BY oid")
    return [r[0] for r in c.fetchall()]


def satznummer(oid, lk):
    return oid - bisect.bisect_left(lk, oid)


def oid_zu_acc(c, acc):
    """OID einer Accession. Ohne Version wird der Praefix gesucht, wie blastdbcmd es tut."""
    c.execute("SELECT oid FROM blast_acc WHERE acc = %s LIMIT 1", (acc,))
    r = c.fetchone()
    if not r and "." not in acc:
        c.execute("SELECT oid FROM blast_acc WHERE acc LIKE %s ORDER BY acc LIMIT 1", (acc + ".%",))
        r = c.fetchone()
    return r[0] if r else None


def ausschnitt(seq, bereich):
    """range=von-bis, 1-basiert und beidseitig inklusiv -- dieselbe Zaehlung wie blastdbcmd."""
    if not bereich:
        return seq
    von, bis = (int(x) for x in bereich.split("-", 1))
    if von < 1 or bis < von:
        raise ValueError(f"range {bereich}: von-bis, ab 1, von <= bis")
    return seq[von - 1:bis]


def pruefen(d, c, n, maskiert=False):
    """Zufaellige Saetze gegen blastdbcmd stellen. Laeuft nur, solange ~/blastdb existiert."""
    lk = luecken(c)
    schlecht = 0
    for i in range(n):
        oid = random.randrange(d.n_seq)
        acc, seq = d.satz(satznummer(oid, lk))
        acc = acc.split()[0]                   # die ID traegt alle Accessions des Satzes
        if not maskiert:
            seq = flicken(c, oid, seq)
        r = subprocess.run([BLASTDBCMD, "-db", BLASTDB, "-entry", acc, "-target_only", "-outfmt", "%s"],
                           capture_output=True, text=True, timeout=60)
        soll = r.stdout.strip().replace("\n", "")
        if soll != seq:
            schlecht += 1
            print(f"ABWEICHUNG oid={oid} acc={acc} dmnd={len(seq)} blast={len(soll)}")
            print(f"  dmnd : {seq[:70]}")
            print(f"  blast: {soll[:70]}")
    print(f"{n - schlecht}/{n} gleich")
    return schlecht == 0


def main():
    p = argparse.ArgumentParser(description="Sequenzen aus der DIAMOND-Datenbank holen")
    p.add_argument("acc", nargs="*", help="Accessions")
    p.add_argument("--db", default=DMND, help=f"dmnd-Datei (Vorgabe {DMND})")
    p.add_argument("--oid", type=int, action="append", help="OID statt Accession, mehrfach moeglich")
    p.add_argument("--range", dest="bereich", default="", help="Teilbereich von-bis, 1-basiert")
    p.add_argument("--roh", action="store_true", help="nur die Residuen, ohne FASTA-Kopf")
    p.add_argument("--maskiert", action="store_true",
                   help="so ausgeben wie makedb es gespeichert hat, ohne blast_mask zurueckzuflicken")
    p.add_argument("--pruefe", type=int, metavar="N", help="N zufaellige Saetze gegen blastdbcmd stellen")
    a = p.parse_args()

    d = Dmnd(a.db)
    import blast_db, pymysql
    c = pymysql.connect(**blast_db.cfg()).cursor()
    if a.pruefe:
        return 0 if pruefen(d, c, a.pruefe, a.maskiert) else 1

    lk = luecken(c)
    fehlt = 0

    def ausgeben(oid):
        name, seq = d.satz(satznummer(oid, lk))
        if not a.maskiert:
            seq = flicken(c, oid, seq)
        seq = ausschnitt(seq, a.bereich)
        print(seq if a.roh else f">{name.split()[0]}\n{seq}")

    for oid in a.oid or []:
        ausgeben(oid)
    for acc in a.acc:
        oid = oid_zu_acc(c, acc)
        if oid is None:
            print(f"{acc}: nicht in der nr", file=sys.stderr)
            fehlt += 1
            continue
        ausgeben(oid)
    return 1 if fehlt else 0


if __name__ == "__main__":
    sys.exit(main())
