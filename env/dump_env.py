#!/usr/bin/env python3
"""Write a conda environment spec for the Tesla P4 / CUDA 11.8 env from its conda-meta directory.

The env was built with micromamba, whose binary is no longer on the machine, so `conda env export`
is not available. conda-meta/*.json is the same source of truth: one file per installed package
with name, version, build and channel.

    dump_env.py <env-prefix> <out.yml> <out-explicit.txt>
"""
import json
import os
import pathlib
import subprocess
import sys

prefix = pathlib.Path(sys.argv[1])
yml, explicit = sys.argv[2], sys.argv[3]
pakete = []
for f in sorted((prefix / "conda-meta").glob("*.json")):
    d = json.loads(f.read_text())
    pakete.append((d["name"], d["version"], d.get("build", ""), d.get("channel", ""), d.get("url", "")))

kanaele = sorted({k.rsplit("/", 1)[0].rsplit("/", 1)[-1] if "/" in k else k
                  for _, _, _, k, _ in pakete if k})
pinned = (prefix / "conda-meta" / "pinned")
pip = subprocess.run([str(prefix / "bin" / "pip"), "freeze"], capture_output=True, text=True).stdout

with open(yml, "w") as o:
    o.write("# Conda environment of the AmyloDeep GPU worker (NVIDIA Tesla P4, compute capability 6.1).\n")
    o.write("# Generated from conda-meta; sm_61 needs CUDA 11.8, it is gone from CUDA 13.\n")
    o.write("name: iver\nchannels:\n")
    for k in kanaele:
        o.write(f"  - {k}\n")
    o.write("dependencies:\n")
    for n, v, b, _, _ in pakete:
        o.write(f"  - {n}={v}={b}\n" if b else f"  - {n}={v}\n")
    if pip.strip():
        o.write("  - pip:\n")
        for z in pip.splitlines():
            # conda packages show up in pip freeze with a feedstock path; they are listed above
            if z.strip() and not z.startswith("-e ") and "@ file:///home/conda/" not in z:
                o.write(f"    - {z.strip()}\n")

with open(explicit, "w") as o:
    o.write("# Exact package URLs of the env, for `conda create --file`.\n@EXPLICIT\n")
    for _, _, _, _, u in pakete:
        if u:
            o.write(u + "\n")

print(f"{len(pakete)} conda packages, {len(pip.splitlines())} pip entries, channels: {', '.join(kanaele)}")
if pinned.exists():
    print("pinned:\n" + pinned.read_text().rstrip())
