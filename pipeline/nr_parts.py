#!/usr/bin/env python3
"""The NCBI nr as a set of partial DIAMOND databases (nr_part_build.py) instead of one nr_full.dmnd.

Every part is registered in wagodb.blast_part (name p<k>_..., path of the .dmnd) and has next to its .dmnd a
sorted <part>.oids.npy: record i of the part is the sequence with OID oids[i]. The records are the ones of the
former nr_full.dmnd, unchanged (built with makedb --no-mask), so blast_mask still restores the masked residues.
A sequence whose accessions belong to taxon groups of several parts is stored in each of them.

The size of the whole nr (sequences = OID end, letters = --dbsize of every search, so that E-values stay
those of a search of the whole nr) is the newest blast_release row with dmnd_letters; its dmnd_hash is the
identifier of the whole state (combined_hash: md5 over name, size and DIAMOND hash of every part). Every update
also writes the state of each part to blast_part_release. Group -> part routing: blast_part_group.

  from nr_parts import NrParts
  nr = NrParts(c)                 # c: cursor on wagodb
  nr.letters, nr.n_seq            # whole nr
  nr.dbs()                        # [(part, dmnd, letters, n_seq), ...] for a search of the whole nr
  nr.record(oid)                  # (id line, residues) as stored
  for oid, name, seq in nr.records(sorted_oids): ...

  python3 nr_parts.py             overview of the parts, coverage check of all OIDs (--check)
  python3 nr_parts.py --register   after the one-time build: release row 'nr-parts' + blast_part_release
"""
import os, sys, hashlib, argparse
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dmnd_getseq import Dmnd

PART_RE = "^p[0-9]+_"


def nr_size(c):
    """(release_id, n_seq, letters, dmnd_hash) of the whole nr: newest blast_release row with dmnd_letters."""
    c.execute("SELECT release_id, dmnd_sequenzen, dmnd_letters, dmnd_hash FROM blast_release "
              "WHERE dmnd_letters IS NOT NULL ORDER BY release_id DESC LIMIT 1")
    r = c.fetchone()
    if not r:
        raise RuntimeError("no blast_release row with dmnd_letters (nr parts not registered)")
    return int(r[0]), int(r[1]), int(r[2]), r[3]


def combined_hash(c):
    """Identifier of the state of all parts: md5 over 'part:n_seq:letters:dmnd_hash' in part order."""
    c.execute("SELECT part, n_seq, letters, dmnd_hash FROM blast_part WHERE part REGEXP %s ORDER BY part", (PART_RE,))
    return hashlib.md5("|".join(":".join(map(str, r)) for r in c.fetchall()).encode()).hexdigest()


def part_release_rows(c, release_id):
    """Write the current state of every part (blast_part) for release_id into blast_part_release."""
    c.execute("REPLACE INTO blast_part_release (release_id, part, n_seq, letters, dmnd_hash) "
              "SELECT %s, part, n_seq, letters, dmnd_hash FROM blast_part WHERE part REGEXP %s", (release_id, PART_RE))


def group_parts(c):
    """{group name: part} from blast_part_group."""
    c.execute("SELECT grp, part FROM blast_part_group")
    return dict(c.fetchall())


class NrParts:
    def __init__(self, c):
        c.execute("SELECT part, dmnd, letters, n_seq FROM blast_part WHERE part REGEXP %s ORDER BY part", (PART_RE,))
        self.parts = [(p, d, int(l), int(n)) for p, d, l, n in c.fetchall()]
        if not self.parts:
            raise RuntimeError("no nr parts registered in blast_part")
        self.release_id, self.n_seq, self.letters, self.hash = nr_size(c)
        self._oids, self._dmnd = {}, {}

    def dbs(self):
        return list(self.parts)

    def oids(self, part):
        if part not in self._oids:
            d = dict((p, d) for p, d, _, _ in self.parts)[part]
            self._oids[part] = np.load(d[:-len(".dmnd")] + ".oids.npy", mmap_mode="r")
        return self._oids[part]

    def dmnd(self, part):
        if part not in self._dmnd:
            self._dmnd[part] = Dmnd(dict((p, d) for p, d, _, _ in self.parts)[part])
        return self._dmnd[part]

    def locate(self, oids):
        """For an array of OIDs: (part index, record number), part index -1 where no part has the OID."""
        oids = np.asarray(oids, dtype=np.uint64)
        where = np.full(len(oids), -1, dtype=np.int16)
        rec = np.zeros(len(oids), dtype=np.int64)
        for k, (p, _, _, _) in enumerate(self.parts):
            todo = np.flatnonzero(where < 0)
            if not len(todo):
                break
            o = self.oids(p)
            i = np.searchsorted(o, oids[todo])
            ok = i < len(o)
            ok[ok] = o[i[ok]] == oids[todo][ok]
            where[todo[ok]] = k
            rec[todo[ok]] = i[ok]
        return where, rec

    def record(self, oid):
        k, r = self.locate([oid])
        if k[0] < 0:
            raise KeyError(f"OID {oid} is in no nr part")
        return self.dmnd(self.parts[k[0]][0]).satz(int(r[0]))

    def records(self, oids, step=1_000_000):
        """(oid, id line, residues) for the OIDs in the given order; KeyError for an OID that is in no part."""
        oids = np.asarray(oids, dtype=np.uint64)
        for b in range(0, len(oids), step):
            chunk = oids[b:b + step]
            k, r = self.locate(chunk)
            if (k < 0).any():
                raise KeyError(f"OID {int(chunk[np.argmax(k < 0)])} is in no nr part")
            for oid, kk, rr in zip(chunk.tolist(), k.tolist(), r.tolist()):
                name, seq = self.dmnd(self.parts[kk][0]).satz(rr)
                yield oid, name, seq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="every OID below n_seq is in at least one part")
    ap.add_argument("--register", action="store_true",
                    help="one time after nr_part_build: release row 'nr-parts' with the size of the whole nr")
    ap.add_argument("--n-seq", type=int, help="with --register: sequences of the whole nr (nr_full header)")
    ap.add_argument("--letters", type=int, help="with --register: letters of the whole nr (nr_full header)")
    a = ap.parse_args()
    import blast_db, pymysql
    c = pymysql.connect(**blast_db.cfg(), autocommit=True).cursor()
    if a.register:
        if not (a.n_seq and a.letters):
            ap.error("--register needs --n-seq and --letters")
        h = combined_hash(c)
        c.execute("INSERT INTO blast_release (quelle, datei, nr_datum, nr_sequenzen, nr_residues, dmnd_datei, dmnd_hash, "
                  "dmnd_sequenzen, dmnd_letters, meta_importiert, aktiv) "
                  "VALUES ('nr-parts', 'nr_part_build', NOW(), 0, 0, 'nr parts', %s, %s, %s, NOW(), 0)",
                  (h, a.n_seq, a.letters))
        rel = c.lastrowid
        part_release_rows(c, rel)
        print(f"registered release {rel}: {a.n_seq} sequences, {a.letters} letters, hash {h}")
    nr = NrParts(c)
    print(f"whole nr: {nr.n_seq} sequences, {nr.letters} letters, release {nr.release_id}, hash {nr.hash}")
    for p, d, l, n in nr.parts:
        print(f"  {p:28s} {n:>12} seq {l / 1e9:8.1f} G letters  {os.path.getsize(d) / 1e9:6.1f} GB  {d}")
    if a.check:
        seen = np.zeros(nr.n_seq, dtype=bool)
        for p, _, _, _ in nr.parts:
            o = np.asarray(nr.oids(p))
            seen[o[o < nr.n_seq].astype(np.int64)] = True
        miss = np.flatnonzero(~seen)
        print(f"OIDs in no part: {len(miss)}" + (f", e.g. {miss[:10].tolist()}" if len(miss) else ""))
        return 1 if len(miss) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
