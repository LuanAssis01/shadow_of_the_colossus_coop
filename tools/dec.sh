#!/bin/sh
# Decompile addresses or symbol names via the running REA session, substituting
# recovered XFF symbol names: ./dec.sh 0xADDR|NAME [...]
cd "$(dirname "$0")"
python3 - "$@" <<'PY'
import re, sys
from rea_session import query

names, addrs = {}, {}
for line in open("../work/symbols.tsv"):
    a, _size, _kind, name = line.rstrip("\n").split("\t")
    v = int(a, 16)
    names.setdefault(v, name)
    addrs.setdefault(name, v)

def label(m):
    v = int(m.group(2), 16)
    return names.get(v, m.group(0))

targets = ["0x%x" % addrs[t] if t in addrs else t for t in sys.argv[1:]]
out = query("batch_decompile", {"addresses": targets})
for it in out["normalized_result"]["items"]:
    v = int(it["address"], 16)
    print("#####", it["address"], names.get(v, ""))
    text = it.get("pseudocode") or str(it.get("error"))[:300]
    print(re.sub(r"\b(FUN|DAT|PTR_DAT|func|PTR_FUN|LAB)_(?:0x)?([0-9a-f]{6,8})\b", label, text))
PY
