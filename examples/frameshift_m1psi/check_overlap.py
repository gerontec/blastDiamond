"""Validate the transcribed +1FS peptide list.

The pool is built from overlapping walks along contiguous +1-frame stretches,
so consecutive peptides should usually share a suffix/prefix. A broken overlap
is either a real break between two stretches or a transcription error.
"""
peps = [l.strip() for l in open('fs_peptides.txt') if l.strip()]
print('n peptides:', len(peps))
bad = [p for p in peps if not set(p) <= set('ACDEFGHIKLMNPQRSTVWY')]
print('non-AA chars:', bad)
print('len range:', min(map(len, peps)), '-', max(map(len, peps)))
print('duplicates:', len(peps) - len(set(peps)))

breaks = 0
for i in range(len(peps) - 1):
    a, b = peps[i], peps[i + 1]
    ov = 0
    for k in range(min(len(a), len(b)), 2, -1):
        if a[-k:] == b[:k]:
            ov = k
            break
    if ov == 0:
        breaks += 1
        print(f'  break {i+1:3d}->{i+2:3d}: {a}  |  {b}')
print('no-overlap transitions:', breaks)
