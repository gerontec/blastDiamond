"""Merge the overlapping peptides fs116-fs120 into their +1 stretch and compare
it residue by residue with AAG23172.1 (human 'unknown', 103 aa, gene PP2150)."""

peps = [l.strip() for l in open('fs_peptides.txt') if l.strip()]
sel = peps[115:120]          # fs116 .. fs120, 1-based in the FASTA ids
print("peptides:")
for i, p in enumerate(sel, start=116):
    print(f"  fs{i:03d}  {p}")

# chain them on their overlaps
merged = sel[0]
for nxt in sel[1:]:
    ov = 0
    for k in range(min(len(merged), len(nxt)), 2, -1):
        if merged[-k:] == nxt[:k]:
            ov = k
            break
    merged += nxt[ov:]
print(f"\nmerged +1 stretch ({len(merged)} aa):\n  {merged}")

aag = ("mgdqvpreqdlakrsspgpspvlnmcnrqsawatalspwycmhamlaapfpswaprvspd"
       "pgsqvcshlhlphsppllvpdtstptwsspiahkrggtrdels").upper()
print(f"\nAAG23172.1 ({len(aag)} aa):\n  {aag}")

# best ungapped offset
best = None
for off in range(-len(merged), len(aag)):
    a = b = 0
    for i, ch in enumerate(merged):
        j = off + i
        if 0 <= j < len(aag):
            b += 1
            a += (ch == aag[j])
    if b >= 20 and (best is None or a > best[0]):
        best = (a, b, off)
a, b, off = best
print(f"\nbest ungapped offset {off}: {a}/{b} identical = {100*a/b:.1f} %")

top = " " * max(0, off) + merged
bot = " " * max(0, -off) + aag
mid = "".join("|" if (i < len(top) and i < len(bot) and top[i] == bot[i]
                      and top[i] != " ") else " "
              for i in range(max(len(top), len(bot))))
w = 60
for s in range(0, max(len(top), len(bot)), w):
    print()
    print("  +1   ", top[s:s + w])
    print("        ", mid[s:s + w])
    print("  AAG  ", bot[s:s + w])
