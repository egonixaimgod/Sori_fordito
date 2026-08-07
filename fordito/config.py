"""Beállítások betöltése/mentése a program melletti JSON fájlba."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields

from .logsetup import get_logger
from .util import app_dir

log = get_logger("beallitasok")

CONFIG_FILENAME = "fordito_beallitasok.json"

# A cél nyelv szándékosan fix: ez a program kizárólag magyarra fordít.
TARGET_LANG = "hu"
TARGET_LANG_ISO3 = "hun"
TRACK_TITLE = "Magyar felirat"

# Alapból védett kifejezések: animékben ezeket a magyar feliratok is
# eredetiben hagyják. A listát a felhasználó bármikor átírhatja, és a program
# a betöltött feliratból új neveket is fel tud ajánlani.
DEFAULT_PROTECTED_TERMS = [
    # általános
    "Shinigami", "senpai", "sensei", "sama", "dojo", "katana", "yokai", "oni",
    # a Bleach szókincse - ha más sorozatot nézel, nyugodtan töröld
    "Soul Society", "Seireitei", "Hueco Mundo", "Karakura",
    "Soul Reaper", "Soul King", "Royal Palace", "Squad Zero",
    "Bankai", "Shikai", "Zanpakuto", "Reiatsu", "Reishi", "Kido",
    "Hollow", "Quincy", "Arrancar", "Espada", "Sternritter", "Vizard",
]


# A beállításfájl változata. Ha nő, a load() egyszeri átállítást végez azokon
# a kapcsolókon, amiknek megváltozott az alapértéke - különben a régi mentés
# csendben felülírná az újat, és a felhasználó a régi viselkedést kapná.
CONFIG_VERSION = 2


@dataclass
class Settings:
    config_version: int = CONFIG_VERSION
    # --- Fordítómotor (mind netes, egyik sem kér kulcsot vagy fiókot) ---
    engine: str = "auto"                 # auto | bing | google
    bing_workers: int = 8                # párhuzamos Microsoft kérések
    google_workers: int = 8              # párhuzamos Google kérések
    source_lang: str = "auto"            # auto vagy ISO-639-1 kód (ja, en, ...)

    # --- Feliratminőség ---
    max_line_length: int = 42            # profi felirat: max ~42 karakter / sor
    max_lines: int = 2
    merge_sentences: bool = True         # feliratokon átnyúló mondatok egyben fordítása
    glossary: dict = field(default_factory=dict)   # "Eredeti": "Magyar" névszótár
    # Amit tilos lefordítani: tulajdonnevek és a világ saját szakszavai.
    # Szabadon szerkeszthető, és a program fel is tudja ajánlani a fájlból.
    protected_terms: list = field(default_factory=lambda: list(DEFAULT_PROTECTED_TERMS))

    # --- MKV kezelés ---
    mux_into_mkv: bool = True            # kész felirat visszaírása az MKV-be
    set_as_default: bool = True          # a magyar sáv legyen az alapértelmezett
    replace_original: bool = True        # az eredeti MKV felülírása a bővítettel
    keep_backup: bool = False            # .bak másolat az eredetiről
    # Alapból NEM tartjuk meg a külön .srt-t, ha a felirat bekerült az MKV-be:
    # a lejátszók a külső fájlt választanák, és két magyar felirat látszana.
    keep_srt_file: bool = False
    # Megjegyzés: a program szándékosan NEM dönt helyetted arról, melyik fájlt
    # kell lefordítani. Minden fájl kipipálva indul a munkalistán, a többi
    # a te dolgod - így soha nem marad ki valami magától.

    # --- Egyéb ---
    recursive_scan: bool = True          # almappák bejárása kötegelt módban
    use_cache: bool = True
    # Friss fordítás: a korábban eltárolt sorokat sem használjuk fel, minden
    # sor újra lefordul. Akkor kell, ha a védett kifejezések listája változott.
    fresh_translation: bool = False
    gui_log_level: str = "INFO"
    last_file_dir: str = ""
    last_folder_dir: str = ""

    # ------------------------------------------------------------------
    @classmethod
    def path(cls) -> str:
        return os.path.join(app_dir(), CONFIG_FILENAME)

    @classmethod
    def load(cls) -> "Settings":
        path = cls.path()
        settings = cls()
        if not os.path.exists(path):
            log.info("Nincs mentett beállítás, alapértelmezésekkel indulunk: %s", path)
            return settings
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            known = {f.name for f in fields(cls)}
            for key, value in data.items():
                if key in known:
                    setattr(settings, key, value)
                else:
                    log.debug("Ismeretlen beállítás a fájlban, kihagyva: %s", key)
            log.info("Beállítások betöltve: %s", path)
            settings._migrate(int(data.get("config_version", 1)))
        except Exception as exc:
            log.warning("A beállításfájl nem olvasható (%s), alapértelmezések lépnek életbe.", exc)
        return settings

    def _migrate(self, from_version: int) -> None:
        """Régi beállításfájl igazítása az új alapértékekhez."""
        if from_version >= CONFIG_VERSION:
            return
        if from_version < 2:
            # A külön .srt megtartása mostantól alapból ki: a lejátszók a külső
            # fájlt választanák a beágyazott sáv helyett, és két magyar felirat
            # jelenne meg. Aki külön fájlt akar, visszakapcsolhatja.
            self.keep_srt_file = False
            log.info("Beállítás frissítve: a külön .srt fájlt már nem tartjuk meg "
                     "(a beágyazott sáv az alapértelmezett).")
        self.config_version = CONFIG_VERSION
        self.save()

    def save(self) -> None:
        path = self.path()
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(asdict(self), handle, ensure_ascii=False, indent=2)
            log.debug("Beállítások mentve: %s", path)
        except Exception as exc:
            log.warning("A beállítások mentése nem sikerült: %s", exc)
