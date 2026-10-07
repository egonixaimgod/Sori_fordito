# Magyar Felirat Fordító

Videók (MKV, MP4, AVI, MOV, WEBM, …) feliratát fordítja **magyarra**. MKV-nál a kész
feliratot kérésre vissza is teszi a videóba alapértelmezett sávként. **Nincs regisztráció, nincs API kulcs, nincs bejelentkezés, nincs
telepítendő modell** – letöltöd az exe-t, elindítod, működik. A leggyengébb laptopon is,
mert minden munkát online fordítószolgáltatások végeznek.

```
  MKV  ──►  felirat kinyerése  ──►  fordítás magyarra  ──►  vissza az MKV-be
                                                            „Magyar felirat” (hun)
                                                            alapértelmezett sávként
```

---

## Mit tud?

| | |
|---|---|
| **Egy fájl vagy egész mappa** | Tallózol egy videót, vagy ráengeded egy mappára – minden videót lefordít benne, almappákkal együtt. |
| **MP4 és más formátumok** | MP4, M4V, MOV, AVI, WEBM, WMV, TS, MPG, FLV is megy. Ezeknél a videóhoz nem nyúl: a magyar felirat külön `_magyar_felirat.srt` fájlba kerül mellé. Ha a videóban nincs beágyazott felirat, a mellette lévő `film.srt` / `film.en.srt` fájlt fordítja le. |
| **Két fordítómotor, kulcs nélkül** | Microsoft fordító (nyelvi modell alapú, ez az elsődleges) és Google Translate (tartalék). Egyikhez sem kell fiók. |
| **Mondategyesítés** | Ha egy mondat két-három felirat között törik ketté, a fordító **egyben** kapja meg, és a kész magyar mondatot osztjuk vissza a feliratokra. Ez a legnagyobb minőségbeli ugrás. |
| **Visszaírás az MKV-be** | A kész felirat bekerül a videóba `Magyar felirat` néven, `hun` nyelvcímkével, alapértelmezett sávként. Videó és hang bitre pontosan másolódik – nincs újrakódolás. |
| **Formázás megőrzése** | Dőlt betű, `{\an8}` pozicionálás, dalszöveg-jelek, párbeszédjelek, ASS tipográfia mind túléli a fordítást. |
| **Névszótár** | Szereplőnevek és visszatérő kifejezések végig ugyanúgy szerepelnek. |
| **Profi sortördelés** | Max. 42 karakter soronként, két sorban, mondathatáron törve. |
| **Gyorsítótár** | Amit egyszer lefordított, többé nem kéri le. Egy évad 2–10. része sokkal gyorsabb, mint az első. |
| **Teljes debug napló** | Minden lépés a `C:\MagyarFeliratFordito\fordito_debug.log` fájlba kerül. |
| **Saját adatmappa** | Beállítás, gyorsítótár, napló mind a `C:\MagyarFeliratFordito` mappában van – az exe mellé (pl. az Asztalra) semmit nem ír. |
| **Automatikus frissítés** | Induláskor megnézi a GitHubon, van-e újabb build, és egy kattintással lecseréli magát. |

---

## Indítás

```
pip install customtkinter
python main.py
```

Ennyi. Az `ffmpeg.exe` és `ffprobe.exe` már a program mellett van, a fordítás pedig a
Python beépített moduljaival megy – nincs más függőség.

**Exe készítése és feltöltés egy lépésben:**

```
rebuild_es_github_push.bat
```

Ez mindent elintéz: ellenőrzi a Pythont és a függőségeket, letölti az ffmpeg-et ha
hiányzik, eggyel növeli a build számot, lefordítja az exe-t, commitol, felpushol a
GitHubra, végül `build-N` néven GitHub Release-t készít az exe-vel. A program
automatikus frissítője ezt a Release-t keresi.

Eredmény: `dist/MagyarFeliratFordito.exe` – **egyetlen fájl, benne az ffmpeg is**.
Bárhova másolható, semmit nem kell mellé telepíteni, csak internet kell neki.

> Az exe ~123 MB, a GitHub fájlonkénti limitje 100 MB, ezért a repóba csak a
> forráskód kerül fel – az exe a Release mellékleteként érhető el (ott 2 GB a limit).

---

## Használat

1. **Mód**: „Egy fájl” vagy „Egész mappa”.
2. **Tallózás** – videó (MKV, MP4, …), vagy külön `.srt` / `.ass` fájl is lehet.
3. **Forrás felirat sáv** – hagyd „Automatikus”-on. A program pontozza a sávokat:
   előnyben részesíti az angolt a japán előtt (megbízhatóbb belőle a fordítás),
   hátrasorolja a „Signs & Songs” és kényszerített sávokat, és kihagyja a képalapúakat.
4. **FORDÍTÁS INDÍTÁSA**.

Közben végig látszik, melyik fájlnál tart, melyik szakaszban, és mennyi van hátra.
Az eseménynapló élőben mutat mindent.

### Eredmény

| Fájl | Mi ez |
|---|---|
| `Anime_S01E01.mkv` | Az eredeti videó, benne a `Magyar felirat` sávval, alapértelmezettként |
| `Anime_S01E01.hu.srt` | Ugyanaz külön feliratfájlként (a lejátszók a `.hu` végződésből felismerik) |

---

## Miért jobb a felirat, mint egy sima Google-fordítás?

A fordítómotor csak az egyik tényező. A minőség nagyobb része azon múlik, **mit** küldünk
neki és **hogyan** rakjuk vissza az eredményt.

### 1. Mondategyesítés a feliratokon át

A feliratokban a mondatok folyton kettétörnek. Ha ezeket külön fordítod, a fordító
félmondatokat lát, és nem tudja, mihez kapcsolódnak:

```
Külön fordítva (a régi mód):        Egyben fordítva (az új mód):
  "Az oka, hogy ma idejöttem"         "Azért jöttem ide ma,"
  "az igazat mondani neked."          "hogy elmondjam neked az igazat."
```

A program felismeri a folytatódó mondatokat (vesszővel záruló felirat, kisbetűs kezdés),
egyben fordíttatja le őket, majd a kész magyar mondatot **tagmondathatáron** osztja
vissza az eredeti feliratokra.

### 2. Formázás leválasztása

A fordító soha nem lát formázókódot. A `{\an8}`, `<i>`, `♪`, `\N` és a párbeszédjelek
leválasztódnak, a tiszta mondat megy át, és a fordítás után minden visszakerül a helyére.
A régi verzió ráküldte a tageket a fordítóra, ami rendszeresen szétverte őket.

### 3. Párbeszédek szétválasztása

Ha egy feliratban két beszélő szerepel (`- Hová mész?` / `- Iskolába megyek.`), a két
mondat külön megy át, és külön sorban jön vissza. Egyben fordítva összefolynának.

### 4. Védett kifejezések – amit tilos lefordítani

A tulajdonneveket és a sorozat saját szakszavait a fordítók lelkesen lefordítják:
a *Soul Society*-ből *„Lélek Társasága”* lesz. Ezért a fordítás idejére jelölőre
cseréljük őket, és utána tesszük vissza:

| | |
|---|---|
| Védelem nélkül | „Holnap megtámadják **a Lélek Társadalmát**.” |
| Védelemmel | „Holnap meg fogják támadni **Soul Society**-et.” |

**Beállítások → Nevek → NE FORDÍTSA**, soronként egy kifejezés. Alapból a gyakori
anime-szakszavak már benne vannak (Shinigami, Bankai, Soul Society, Quincy…) –
más sorozatnál nyugodtan írd át.

A **„🔍 Nevek keresése a fájlból”** gomb végigolvassa a kiválasztott felirat
szövegét, és felajánlja a benne szereplő neveket. Azt keresi, ami nagybetűs, de nem
mondat elején áll, és legalább kétszer előfordul – ezek jellemzően tulajdonnevek.

A visszahelyettesítés után a magyar névelőt is javítjuk: a fordító a jelölő alapján
döntene („Az Soul Society”), a szabály viszont egyértelmű – magánhangzó előtt *az*,
mássalhangzó előtt *a*.

### 5. Névszótár

**Beállítások → Nevek → NÉVSZÓTÁR**, soronként `eredeti = magyar`. A csere a fordítás
**előtt** történik, és az eredmény automatikusan védetté válik. Így a japán írásjelekkel
írt nevek is végig ugyanabban a magyar alakban szerepelnek.

---

## Beállítások

### MKV kimenet
- **A magyar felirat kerüljön bele az MKV-be** – be
- **Legyen ez az alapértelmezett felirat sáv** – be (a többiről lekerül a jelölés)
- **Az eredeti MKV felülírása** – be. Kikapcsolva külön `.hu.mkv` készül.
- **Biztonsági másolat (.bak)** – ki (sok helyet foglal)
- **Kihagyás, ha már van benne magyar felirat** – be. Ezért bármikor újraindítható
  egy félbehagyott mappa: a kész részeket átugorja.

### Fordítás
- **Feliratokon átnyúló mondatok egyben fordítása** – be (lásd fentebb)
- **Sor hossza / sorok száma** – 42 / 2, ez a feliratszabvány
- **Forrásnyelv** – `auto`; a sáv nyelvcímkéjéből és az írásjelekből ismeri fel

### Haladó
- **Párhuzamos kérések** – 8 mindkét motorra. Túl sok esetén a szolgáltatás
  átmenetileg korlátoz, ilyenkor a program magától lassít és újrapróbál.

---

## Biztonságos-e a fájljaimra?

Igen, több okból:

- A videó és a hang **`-c copy`**-val másolódik: egyetlen képkockát sem kódol újra.
- Az új fájl előbb ideiglenes néven készül el, és csak akkor lép az eredeti helyére, ha
  **átment az ellenőrzésen** (benne van-e a magyar sáv, egyezik-e a játékidő).
- Ha bármi hiba történik, az ideiglenes fájl törlődik, az eredeti érintetlen marad.
- A `.hu.srt` mindig megmarad a videó mellett, tehát a fordítás sosem vész el.

---

## Mi lett jobb a régi verzióhoz képest?

**A legfontosabb hibajavítás.** A régi kód 50 feliratsort fűzött össze sortörésekkel,
elküldte a Google-nek, majd a választ sortörésnél vágta szét. A Google viszont
összevonja és szétszedi a sorokat – amint a darabszám eltért, a kód üres sorokkal
pótolt és levágta a végét. Ettől **a felirat elcsúszott az idővonalon, és percekre
kiürült**. Pontosan ez volt az, amitől „elbassza a feliratot”.

Most minden kérés külön megy: a hozzárendelés matematikailag sem tud elcsúszni.

| | Régi | Új |
|---|---|---|
| Sorcsúszás | Rendszeres | Lehetetlen |
| Fordítás minősége | Google, soronként, szövegkörnyezet nélkül | Microsoft (nyelvi modell), egész mondatokkal |
| Sebesség | Soronként, sorban | 8 párhuzamos szál + gyorsítótár |
| Formázás | `\n`-t szóközre cserélte, tageket ráküldte a fordítóra | Leválasztja, majd visszateszi |
| Hibás sor | Üresen maradt | Tartalék motor javítja |
| Kötegelt mód | Nincs | Egész mappa, almappákkal |
| Visszaírás MKV-be | Nincs | Alapértelmezett `Magyar felirat` sáv |
| Napló | Csak a képernyőn | `fordito_debug.log`, forgatott, teljes |
| API kulcs | Kellett (OpenAI) | Nem kell semmi |

---

## Felépítés

```
main.py                    belépési pont
fordito/
  config.py                beállítások (JSON a program mellett)
  logsetup.py              naplózás: fájl + élő felület
  util.py                  segédfüggvények
  subtitles.py             SRT/ASS olvasás-írás, formázásvédelem, sortördelés
  media.py                 ffprobe/ffmpeg: sávelemzés, kinyerés, muxolás
  cache.py                 SQLite fordításgyorsítótár
  translator.py            vezénylés: mondategyesítés, névszótár, cache, tartalék
  pipeline.py              teljes folyamat, egy fájlra és egész mappára
  gui.py                   felület
  engines/
    base.py                közös motorfelület
    bing.py                Microsoft fordító (elsődleges)
    google.py              Google Translate (tartalék)
```

A régi verzió (streamlit app + az eredeti `main.py`) már nincs a mappában, de a git
history-ban megmaradt – ha kellene: `git show df673be:legacy/main_regi.py`

```
```

A program által generált fájlok (mind a program mellett):

- `fordito_debug.log` – teljes napló, 3 MB-onként forgatva, 5 példány
- `fordito_beallitasok.json` – beállítások
- `fordito_cache.sqlite` – fordításgyorsítótár

---

## Hibakeresés

**Minden a `C:\MagyarFeliratFordito\fordito_debug.log` fájlban van** – a felületen a „📄 Debug napló megnyitása”
gombbal nyílik. Benne van minden ffmpeg parancs, minden sávdöntés, minden fordítási
kérés, minden újrapróbálkozás és minden hiba a teljes veremmel.

| Tünet | Ok / megoldás |
|---|---|
| „Csak képalapú felirat van benne” | PGS/VobSub sáv, képekből áll. Szöveg nincs benne, OCR kellene hozzá. |
| „Nincs benne felirat sáv, és mellette sincs feliratfájl” | A videóban nincs beépített felirat, és azonos nevű `.srt` / `.ass` sincs mellette. |
| „Egyik fordítószolgáltatás sem érhető el” | Nincs internet, vagy tűzfal/proxy blokkol. |
| Egy-egy sor eredeti nyelven marad | Mindkét motor elhasalt rajta. A napló megmondja, melyiken és miért. |
| Lassú | Csökkentsd a párhuzamos kérések számát – ha a szolgáltatás korlátoz, a várakozás lassít. |

> A fordítószolgáltatások a böngészőjükkel megegyező módon érhetők el. Ha valamelyik
> megváltoztatja a felületét, az adott motor kieshet – ilyenkor a program automatikusan
> a másikra vált, és ezt a naplóba is beírja.
