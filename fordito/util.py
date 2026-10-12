"""Alap segédfüggvények: útvonalak, folyamatindítás, formázás."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

# Windows: ne villanjon fel konzolablak a háttérfolyamatoknál.
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def app_dir() -> str:
    """A program melletti könyvtár (exe mellett fagyasztva, különben a projekt gyökere)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


DATA_DIR_NAME = "MagyarFeliratFordito"

# Amit a program régen az exe mellé írt - a data_dir() első hívásakor átköltöznek.
_OLD_FILE_PREFIXES = ("fordito_beallitasok.json", "fordito_cache.sqlite", "fordito_debug.log")

_data_dir: str | None = None
migrated_files: list[str] = []      # a napló indulása után kiírjuk, mi költözött


def data_dir() -> str:
    """A program saját adatmappája (beállítások, cache, napló, frissítés) a
    rendszerlemez gyökerében, NEM az exe mellett - így az Asztalra húzott exe
    nem szemeteli tele az Asztalt, és mindig ugyanott van minden, bárhonnan
    indítják. Ha a gyökérbe nem írhatunk, a felhasználó AppData mappájába megy."""
    global _data_dir
    if _data_dir:
        return _data_dir
    candidates = [os.path.join(os.environ.get("SystemDrive", "C:") + "\\", DATA_DIR_NAME)]
    if os.environ.get("LOCALAPPDATA"):
        candidates.append(os.path.join(os.environ["LOCALAPPDATA"], DATA_DIR_NAME))
    for path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".irhato")
            with open(probe, "w") as handle:
                handle.write("ok")
            os.remove(probe)
        except OSError:
            continue
        _data_dir = path
        break
    else:
        _data_dir = app_dir()       # végső eset: a régi viselkedés
    _migrate_old_files(_data_dir)
    return _data_dir


def _migrate_old_files(target: str) -> None:
    """A korábban az exe mellé írt fájlok átköltöztetése az adatmappába.
    Ha a célban már van ilyen fájl, azt nem írjuk felül (a régi marad a helyén)."""
    source = app_dir()
    if os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(target)):
        return
    try:
        names = os.listdir(source)
    except OSError:
        return
    for name in names:
        if not name.startswith(_OLD_FILE_PREFIXES):
            continue
        old = os.path.join(source, name)
        new = os.path.join(target, name)
        if not os.path.isfile(old) or os.path.exists(new):
            continue
        try:
            shutil.move(old, new)
            migrated_files.append(name)
        except OSError:
            pass


def resource_dir() -> str:
    """Csomagolt erőforrások könyvtára (PyInstaller kicsomagolt mappa vagy projekt gyökér)."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_process(command: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    """Külső program futtatása konzolablak nélkül, UTF-8 kimenettel."""
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_NO_WINDOW,
    )


def popen_process(command: list[str]) -> subprocess.Popen:
    """Folyamatos kimenetű külső program (pl. ffmpeg progress) indítása."""
    return subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=_NO_WINDOW,
    )


def human_time(seconds: float) -> str:
    """Másodperc -> "2p 14mp" alakú, olvasható időtartam."""
    if seconds is None or seconds < 0 or seconds != seconds:  # NaN-védelem
        return "?"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} mp"
    if seconds < 3600:
        return f"{seconds // 60}p {seconds % 60:02d}mp"
    return f"{seconds // 3600}ó {(seconds % 3600) // 60:02d}p"


def human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


def shorten_path(path: str, max_len: int = 60) -> str:
    """Hosszú útvonal rövidítése a naplóhoz/felülethez."""
    if len(path) <= max_len:
        return path
    head, tail = os.path.split(path)
    if len(tail) >= max_len - 4:
        return "..." + tail[-(max_len - 3):]
    keep = max_len - len(tail) - 4
    return head[:keep] + "...\\" + tail


class Stopwatch:
    """Egyszerű időmérő a teljesítménynaplózáshoz."""

    def __init__(self) -> None:
        self.start = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self.start

    def reset(self) -> None:
        self.start = time.monotonic()


_MPC_KEY = r"Software\MPC-HC\MPC-HC"


def reset_mpc_subtitle_choice(video_paths: list[str], language: str = "hun") -> None:
    """Az MPC-HC ne bírálja felül a videó alapértelmezett felirat sávját.

    Az MPC-HC fájlonként megjegyzi a legutóbb választott feliratot, újranyitáskor
    azt állítja vissza, és a kedvenc nyelvét (pl. eng) is a default sáv elé veszi.
    Ezért töröljük a fájlhoz megjegyzett választást, és a magyart tesszük előre.
    shortcut: csak a registry-s MPC-HC beállítást kezeli, az .ini-s (hordozható) módot nem.
    """
    if os.name != "nt":
        return
    import winreg

    targets = {os.path.normcase(os.path.abspath(p)) for p in video_paths}
    access = winreg.KEY_READ | winreg.KEY_SET_VALUE
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _MPC_KEY + r"\MediaHistory") as history:
            count = winreg.QueryInfoKey(history)[0]
            for name in [winreg.EnumKey(history, i) for i in range(count)]:
                with winreg.OpenKey(history, name, 0, access) as entry:
                    values = [winreg.EnumValue(entry, i) for i in range(winreg.QueryInfoKey(entry)[1])]
                    filename = next((v[1] for v in values if v[0] == "Filename"), "")
                    if os.path.normcase(os.path.abspath(filename)) not in targets:
                        continue
                    # A megjegyzett sávszám és a hozzáadott külső feliratfájlok (Sub001, ...).
                    for value_name, _, _ in values:
                        if value_name == "SubtitleTrackIndex" or (
                                value_name.startswith("Sub") and value_name[3:].isdigit()):
                            winreg.DeleteValue(entry, value_name)
    except OSError:
        pass  # nincs MPC-HC, vagy nincs előzmény

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _MPC_KEY + r"\Settings", 0, access) as settings:
            order = str(winreg.QueryValueEx(settings, "SubtitlesLanguageOrder")[0])
            codes = [c for c in order.replace(",", " ").replace(";", " ").split() if c.lower() != language]
            new_order = " ".join([language, *codes])
            if new_order != order:
                winreg.SetValueEx(settings, "SubtitlesLanguageOrder", 0, winreg.REG_SZ, new_order)
    except OSError:
        pass
