"""Alap segédfüggvények: útvonalak, folyamatindítás, formázás."""

from __future__ import annotations

import os
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
