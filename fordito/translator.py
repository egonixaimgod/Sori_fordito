"""Fordítás vezénylése: mondategyesítés, ismétlésszűrés, gyorsítótár, tartalék.

Ez a réteg köti össze a feliratkezelést a motorokkal, és itt lakik a
feliratminőség négy legfontosabb trükkje:

  1. Mondategyesítés   - ha egy mondat két felirat között törik ketté, a
                         fordító EGYBEN kapja meg, majd a kész magyar mondatot
                         osztjuk vissza a feliratokra. Enélkül a gépi fordító
                         félmondatokat kap, és rendre félrefordítja őket.
  2. Névszótár         - a megadott nevek fordítás előtt a végleges magyar
                         alakjukra cserélődnek, így végig egyformák maradnak.
  3. Ismétlésszűrés    - az azonos szövegeket egyszer fordítjuk le.
  4. Gyorsítótár       - amit egyszer lefordítottunk, többé nem kérjük le.

Ha az elsődleges motor kihagy egy sort, a tartalék befejezi, így üres
feliratsor soha nem marad a fájlban.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .cache import TranslationCache
from .engines import BingEngine, Engine, EngineError, GoogleEngine
from .logsetup import get_logger
from .subtitles import prepare_line
from .util import human_time

log = get_logger("fordito")

# (kész, összes, fázis szövege)
ProgressCallback = Callable[[int, int, str], None]

_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")
_HANGUL = re.compile(r"[가-힯]")
_CYRILLIC = re.compile(r"[а-яА-Я]")

# Mondatvégi írásjelek - ha ezzel zárul a felirat, nincs mit egyesíteni.
_TERMINAL = ".!?…:。！？：»\"'”’)]"
# Kelet-ázsiai nyelvekben nincs kis- és nagybetű, ezért ott a folytatást
# csak kiírt vessző jelzi megbízhatóan.
_CASELESS_LANGS = {"ja", "zh", "ko", "th"}
_CONTINUATION = ("、", ",", "…", "-", "—", "–")

MAX_MERGE_CUES = 3      # legfeljebb ennyi feliratot vonunk össze
MAX_MERGE_CHARS = 320   # és csak ha az együttes hossz ésszerű marad

# ISO-639-2 -> ISO-639-1 a fordítómotorokhoz
LANG_MAP = {
    "jpn": "ja", "eng": "en", "ger": "de", "deu": "de", "fre": "fr", "fra": "fr",
    "spa": "es", "ita": "it", "rus": "ru", "kor": "ko", "chi": "zh", "zho": "zh",
    "pol": "pl", "cze": "cs", "ces": "cs", "rum": "ro", "ron": "ro", "por": "pt",
    "dut": "nl", "nld": "nl", "swe": "sv", "tur": "tr", "ara": "ar",
}


@dataclass
class TranslationStats:
    total_cues: int = 0
    total_units: int = 0
    merged_groups: int = 0
    unique_units: int = 0
    from_cache: int = 0
    from_primary: int = 0
    from_fallback: int = 0
    failed: int = 0
    seconds: float = 0.0
    engine_name: str = ""
    source_lang: str = "auto"
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        speed = (self.unique_units / self.seconds) if self.seconds > 0 else 0
        merged = f", {self.merged_groups} mondat egyesítve" if self.merged_groups else ""
        return (
            f"{self.total_cues} felirat | {self.unique_units} egyedi kérés{merged} | "
            f"gyorsítótárból {self.from_cache}, fordítva {self.from_primary}, "
            f"tartalék {self.from_fallback}, sikertelen {self.failed} | "
            f"{human_time(self.seconds)} ({speed:.1f} sor/mp)"
        )


def detect_language(texts: list[str], track_language: str = "") -> str:
    """A forrásnyelv megállapítása a sáv címkéjéből, illetve az írásjelekből."""
    code = LANG_MAP.get((track_language or "").lower(), (track_language or "").lower()[:2])
    if code and code not in ("un", ""):
        return code

    sample = " ".join(texts[:200])
    if _CJK.search(sample):
        return "ja"
    if _HANGUL.search(sample):
        return "ko"
    if _CYRILLIC.search(sample):
        return "ru"
    return "auto"


# =========================================================================
#  Mondategyesítés
# =========================================================================

def _continues(previous: str, following: str, caseless: bool) -> bool:
    """Egyetlen mondat két fele-e a két feliratszöveg?"""
    previous, following = previous.strip(), following.strip()
    if not previous or not following:
        return False
    if previous[-1] in _TERMINAL:
        return False            # az előző mondat lezárult
    if following[0] in "-–—":
        return False            # új beszélő
    if previous.endswith(_CONTINUATION):
        # Vesszővel vagy gondolatjellel záruló felirat mindig folytatódik -
        # akkor is, ha a következő nagybetűvel kezdődik (pl. "I", nevek).
        return True
    if caseless:
        # Japán/kínai/koreai: itt nincs kis- és nagybetű, és a mondatvégi pont
        # gyakran hiányzik, ezért ennél többet nem kockáztatunk.
        return False
    # Latin betűs nyelvek: a kisbetűs kezdés egyértelmű folytatás.
    return following[0].islower()


def build_groups(units: list[str], unit_cue: list[int], layout: list[int],
                 source_lang: str, enabled: bool) -> list[list[int]]:
    """Az egy mondathoz tartozó szövegdarabok összefogása csoportokba."""
    if not enabled:
        return [[i] for i in range(len(units))]

    caseless = (source_lang or "")[:2] in _CASELESS_LANGS
    groups: list[list[int]] = []
    current: list[int] = []

    for i, text in enumerate(units):
        if not current:
            current = [i]
            continue

        previous = current[-1]
        mergeable = (
            layout[unit_cue[previous]] == 1        # az előző felirat egyetlen darab
            and layout[unit_cue[i]] == 1           # és ez is
            and unit_cue[i] == unit_cue[previous] + 1   # szomszédos feliratok
            and len(current) < MAX_MERGE_CUES
            and sum(len(units[k]) for k in current) + len(text) <= MAX_MERGE_CHARS
            and _continues(units[previous], text, caseless)
        )
        if mergeable:
            current.append(i)
        else:
            groups.append(current)
            current = [i]

    if current:
        groups.append(current)
    return groups


def redistribute(translated: str, parts: list[str]) -> list[str]:
    """A lefordított mondat visszaosztása az eredeti feliratok között.

    Az arányt az eredeti darabok hossza adja, de a vágás lehetőleg tagmondat-
    határra esik: a vessző vagy pont utáni törés sokkal olvashatóbb feliratot
    ad, mint a szigorúan arányos darabolás.
    """
    count = len(parts)
    if count == 1:
        return [translated.strip()]

    words = translated.split()
    if len(words) < count:
        # Nem osztható szét értelmesen: az egész az elsőbe kerül.
        return [translated.strip()] + [""] * (count - 1)

    weights = [max(len(part), 1) for part in parts]
    total_weight = sum(weights)
    total_chars = len(translated)

    # Az egyes szavak utáni karakterpozíciók.
    positions: list[int] = []
    running = 0
    for word in words:
        running += len(word) + 1
        positions.append(running - 1)

    breaks: list[int] = []
    previous = 0
    for k in range(count - 1):
        target = total_chars * (sum(weights[: k + 1]) / total_weight)
        lowest = previous + 1                       # ebbe a részbe jusson legalább egy szó
        highest = len(words) - (count - k - 1)      # és a maradéknak is
        best_index, best_score = lowest, float("inf")
        for index in range(lowest, highest + 1):
            score = abs(positions[index - 1] - target)
            ending = words[index - 1][-1:]
            if ending in ".!?…":
                score -= 16     # mondathatár: ideális vágás
            elif ending in ",;:":
                score -= 10     # tagmondathatár: jó vágás
            if score < best_score:
                best_index, best_score = index, score
        breaks.append(best_index)
        previous = best_index

    result: list[str] = []
    start = 0
    for position in breaks:
        result.append(" ".join(words[start:position]))
        start = position
    result.append(" ".join(words[start:]))
    return result


def apply_glossary(text: str, glossary: dict) -> str:
    """Névszótár: a forrásszöveg neveit a végleges magyar alakra cseréljük.

    Fordítás ELŐTT történik, mert a latin betűs tulajdonneveket a fordítók
    érintetlenül hagyják - így végig ugyanaz a név szerepel.
    """
    if not glossary:
        return text
    for source, target in glossary.items():
        if source and target and source in text:
            text = text.replace(source, target)
    return text


# =========================================================================
#  Vezénylés
# =========================================================================

class Translator:
    def __init__(self, settings, cache: Optional[TranslationCache] = None):
        self.settings = settings
        self.cache = cache or TranslationCache(enabled=getattr(settings, "use_cache", True))
        self._engine_cache: dict[str, Engine] = {}

    def get_engine(self, name: str) -> Engine:
        if name not in self._engine_cache:
            self._engine_cache[name] = (
                GoogleEngine(self.settings) if name == "google" else BingEngine(self.settings)
            )
        return self._engine_cache[name]

    def choose_engines(self) -> tuple[Engine, Optional[Engine]]:
        """Elsődleges és tartalék motor. Mindkettő netes, egyik sem kér kulcsot."""
        preference = getattr(self.settings, "engine", "auto")
        if preference == "google":
            return self.get_engine("google"), self.get_engine("bing")
        if preference == "bing":
            return self.get_engine("bing"), self.get_engine("google")

        # Automatikus: a Microsoft a jobb, a Google a biztos háló alatta.
        bing = self.get_engine("bing")
        status = bing.status()
        if status.available:
            log.info("Motor: Microsoft fordító, tartalék: Google Translate.")
            return bing, self.get_engine("google")
        log.warning("A Microsoft fordító nem elérhető (%s) - Google Translate lesz.", status.message)
        return self.get_engine("google"), None

    # ------------------------------------------------------------------
    def translate_document(
        self,
        document,
        track_language: str = "",
        progress: Optional[ProgressCallback] = None,
        cancel: Optional[threading.Event] = None,
    ) -> TranslationStats:
        """Egy betöltött feliratdokumentum lefordítása magyarra."""
        started = time.monotonic()
        stats = TranslationStats()

        texts = document.get_texts()
        stats.total_cues = len(texts)
        if not texts:
            stats.notes.append("A felirat üres volt.")
            return stats

        configured = getattr(self.settings, "source_lang", "auto")
        source_lang = (configured if configured and configured != "auto"
                       else detect_language(texts, track_language))
        stats.source_lang = source_lang
        log.info("Forrásnyelv: %s (sávcímke: '%s')", source_lang, track_language or "nincs")

        # --- 1. Előkészítés: formázás leválasztása, párbeszédek szétbontása.
        prepared = [prepare_line(text) for text in texts]
        units: list[str] = []
        unit_cue: list[int] = []
        layout: list[int] = []
        for cue_index, line in enumerate(prepared):
            line_texts = line.texts
            layout.append(len(line_texts))
            for text in line_texts:
                units.append(text)
                unit_cue.append(cue_index)
        stats.total_units = len(units)
        log.info("%d feliratból %d fordítandó szövegdarab lett.", len(texts), len(units))

        if not units:
            stats.notes.append("Nem volt fordítható szöveg a feliratban.")
            return stats

        # --- 2. Mondategyesítés a feliratokon át.
        groups = build_groups(units, unit_cue, layout, source_lang,
                              bool(getattr(self.settings, "merge_sentences", True)))
        stats.merged_groups = sum(1 for g in groups if len(g) > 1)
        if stats.merged_groups:
            log.info("%d ketté(vagy több felé) tört mondat lesz egyben lefordítva.",
                     stats.merged_groups)
            # Az egyesített mondat folytatásai nem mondatkezdők: ott nem szabad
            # nagybetűre javítani, különben mondat közepén lenne nagybetű.
            for group in groups:
                for unit_index in group[1:]:
                    for piece in prepared[unit_cue[unit_index]].pieces:
                        if piece.translatable:
                            piece.capitalize = False
                            break

        glossary = getattr(self.settings, "glossary", None) or {}
        group_texts = [
            apply_glossary(" ".join(units[i] for i in group), glossary) for group in groups
        ]

        # --- 3. Ismétlődések összevonása.
        unique_texts: list[str] = []
        unique_index: dict[str, int] = {}
        group_to_unique: list[int] = []
        for text in group_texts:
            if text not in unique_index:
                unique_index[text] = len(unique_texts)
                unique_texts.append(text)
            group_to_unique.append(unique_index[text])
        stats.unique_units = len(unique_texts)
        saved = len(group_texts) - len(unique_texts)
        if saved:
            log.info("Ismétlésszűrés: %d kéréssel kevesebb kell.", saved)

        primary, fallback = self.choose_engines()
        stats.engine_name = primary.describe()

        # --- 4. Gyorsítótár.
        translations: list[str] = [""] * len(unique_texts)
        pending: list[int] = []
        for i, text in enumerate(unique_texts):
            hit = self.cache.get(text, primary.name, "", source_lang)
            if hit:
                translations[i] = hit
            else:
                pending.append(i)
        stats.from_cache = len(unique_texts) - len(pending)
        if stats.from_cache:
            log.info("Gyorsítótárból azonnal megvan %d kérés.", stats.from_cache)

        # --- 5. Fordítás.
        if pending and not (cancel and cancel.is_set()):
            phase = f"Fordítás ({primary.display_name})"
            if progress:
                progress(0, len(pending), phase)

            batch = [unique_texts[i] for i in pending]
            try:
                produced = primary.translate(
                    batch, source_lang,
                    (lambda done, total: progress(done, total, phase)) if progress else None,
                    cancel,
                )
            except EngineError as exc:
                log.error("Az elsődleges motor elhasalt: %s", exc)
                stats.notes.append(f"{primary.display_name}: {exc}")
                produced = [""] * len(batch)

            still_missing: list[int] = []
            for offset, index in enumerate(pending):
                value = produced[offset] if offset < len(produced) else ""
                if value.strip():
                    translations[index] = value.strip()
                    stats.from_primary += 1
                else:
                    still_missing.append(index)

            # --- 6. Tartalék motor a maradékra.
            if still_missing and fallback and not (cancel and cancel.is_set()):
                log.info("Tartalék motor indul %d kérésre (%s).",
                         len(still_missing), fallback.display_name)
                phase = f"Tartalék ({fallback.display_name})"
                try:
                    rescued = fallback.translate(
                        [unique_texts[i] for i in still_missing], source_lang,
                        (lambda done, total: progress(done, total, phase)) if progress else None,
                        cancel,
                    )
                    for offset, index in enumerate(still_missing):
                        if offset < len(rescued) and rescued[offset].strip():
                            translations[index] = rescued[offset].strip()
                            stats.from_fallback += 1
                except EngineError as exc:
                    log.error("A tartalék motor is elhasalt: %s", exc)
                    stats.notes.append(f"Tartalék motor: {exc}")

            self.cache.put_many(
                [(unique_texts[i], translations[i]) for i in pending if translations[i]],
                primary.name, "", source_lang,
            )

        # --- 7. Sikertelen kérések: marad az eredeti szöveg, hogy ne tűnjön el felirat.
        stats.failed = sum(1 for value in translations if not value)
        if stats.failed:
            log.warning("%d kérés fordítatlan maradt, ezek az eredeti szöveggel maradnak.",
                        stats.failed)
            for i, value in enumerate(translations):
                if not value:
                    translations[i] = unique_texts[i]

        # --- 8. Az egyesített mondatok visszaosztása a feliratokra.
        unit_translations: list[str] = [""] * len(units)
        for group_index, group in enumerate(groups):
            whole = translations[group_to_unique[group_index]]
            pieces = redistribute(whole, [units[i] for i in group])
            for slot, unit_index in enumerate(group):
                value = pieces[slot] if slot < len(pieces) else ""
                # Üresen maradt darab: inkább az eredeti, mint a semmi.
                unit_translations[unit_index] = value or units[unit_index]

        # --- 9. Visszaépítés: formázás vissza, profi sortördeléssel.
        max_length = int(getattr(self.settings, "max_line_length", 42))
        max_lines = int(getattr(self.settings, "max_lines", 2))
        output: list[str] = []
        cursor = 0
        for line, count in zip(prepared, layout):
            pieces = unit_translations[cursor:cursor + count]
            cursor += count
            output.append(line.render(pieces, max_length, max_lines))
        document.set_texts(output)

        stats.seconds = time.monotonic() - started
        log.info("Fordítás kész: %s", stats.summary())
        log.info("Gyorsítótár: %s", self.cache.stats())
        return stats
