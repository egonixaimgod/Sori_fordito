# -*- coding: utf-8 -*-
"""A kiadás-szkript segédje: a BUILD_SZAM növelése.

A helyi fordito/__init__.py-ban lévő szám mellett a GitHubon már publikáltat is
lekérdezi, és a NAGYOBBIKBÓL indul ki. Enélkül egy elavult helyi checkout-ról
futtatott kiadás visszaléptetné a publikált build-számot.

Két távoli forrást nézünk: a raw.githubusercontent.com CDN mögött ül (kb. 5 perc
cache), így két gyors egymás utáni kiadásnál a régi számot adná vissza - a
GitHub Releases API (`build-N` címkék) viszont azonnal friss. A három szám
(helyi / raw / kiadás) maximumából emelünk.

A version_info.txt-be sütött Windows fájlverziót is szinkronban tartja.
"""

import json
import re
import ssl
import urllib.request

REPO = "egonixaimgod/Sori_fordito"
FORRAS = "fordito/__init__.py"
VERZIO_INFO = "version_info.txt"
TAVOLI_URL = f"https://raw.githubusercontent.com/{REPO}/main/{FORRAS}"
KIADASOK_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=30"

BUILD_MINTA = re.compile(r"^BUILD_SZAM\s*=\s*(\d+)", re.M)
CIMKE_MINTA = re.compile(r"^build-(\d+)$", re.I)

with open(FORRAS, "r", encoding="utf-8") as f:
    tartalom = f.read()

talalat = BUILD_MINTA.search(tartalom)
if not talalat:
    raise SystemExit(f"Nem találom a BUILD_SZAM sort a {FORRAS}-ban!")
helyi = int(talalat.group(1))

ctx = ssl.create_default_context()

tavoli = helyi
try:
    keres = urllib.request.Request(TAVOLI_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(keres, context=ctx, timeout=15) as valasz:
        tavoli_forras = valasz.read().decode("utf-8", errors="replace")
    tavoli_talalat = BUILD_MINTA.search(tavoli_forras)
    if tavoli_talalat:
        tavoli = int(tavoli_talalat.group(1))
except Exception:
    pass          # nincs net / GitHub elérhetetlen - a helyi számból indulunk ki

kiadas = helyi
try:
    keres = urllib.request.Request(KIADASOK_URL, headers={
        "User-Agent": "MagyarFeliratFordito-bump",
        "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(keres, context=ctx, timeout=15) as valasz:
        adat = json.loads(valasz.read().decode("utf-8"))
    for rel in (adat if isinstance(adat, list) else []):
        m = CIMKE_MINTA.match((rel.get("tag_name") or "").strip())
        if m:
            kiadas = max(kiadas, int(m.group(1)))
except Exception:
    pass

uj = max(helyi, tavoli, kiadas) + 1
tartalom = tartalom[:talalat.start(1)] + str(uj) + tartalom[talalat.end(1):]
with open(FORRAS, "w", encoding="utf-8") as f:
    f.write(tartalom)

# A Windows numerikus verziómezője kötelezően négy elemű: a build a harmadik helyre kerül.
with open(VERZIO_INFO, "r", encoding="utf-8") as f:
    vi = f.read()
vi = re.sub(r"filevers=\((\d+), (\d+), \d+, 0\)", rf"filevers=(\1, \2, {uj}, 0)", vi)
vi = re.sub(r"prodvers=\((\d+), (\d+), \d+, 0\)", rf"prodvers=(\1, \2, {uj}, 0)", vi)
vi = re.sub(r"(u'FileVersion', u'\d+\.\d+\.)\d+(\.0')", rf"\g<1>{uj}\g<2>", vi)
vi = re.sub(r"(u'ProductVersion', u'\d+\.\d+\.)\d+(\.0')", rf"\g<1>{uj}\g<2>", vi)
with open(VERZIO_INFO, "w", encoding="utf-8") as f:
    f.write(vi)

print(uj)
