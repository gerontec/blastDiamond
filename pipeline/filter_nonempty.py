#!/usr/bin/env python3
import sys

def main():
    inp = sys.stdin.buffer
    out = sys.stdout.buffer
    header = None
    seq_parts = []
    n_kept = 0
    n_dropped = 0

    def flush():
        nonlocal n_kept, n_dropped
        if header is None:
            return
        if seq_parts:
            out.write(header)
            for p in seq_parts:
                out.write(p)
            n_kept += 1
        else:
            n_dropped += 1

    for line in inp:
        if line.startswith(b">"):
            flush()
            header = line
            seq_parts = []
        else:
            if line.strip():
                seq_parts.append(line)
    flush()
    sys.stderr.write(f"filter_nonempty: kept={n_kept} dropped_empty={n_dropped}\n")

if __name__ == "__main__":
    main()
