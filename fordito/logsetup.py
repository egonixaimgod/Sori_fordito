"""Naplózás: minden az adatmappa (C:/MagyarFeliratFordito) fordito_debug.log fájljába kerül.

A napló szándékosan bőbeszédű - ez a fejlesztés és a hibakeresés alapja.
A felület élőben ugyanezt a folyamot mutatja egy sorbaállított handleren keresztül.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import queue
import sys
from typing import Optional

from . import util
from .util import app_dir, data_dir

LOG_FILENAME = "fordito_debug.log"
_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(threadName)-14s | %(name)-18s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# A felület ebből a sorból olvassa ki az élő naplót.
gui_queue: "queue.Queue[tuple[int, str]]" = queue.Queue(maxsize=10000)

_log_path: Optional[str] = None
_configured = False


class _GuiQueueHandler(logging.Handler):
    """A naplórekordokat egy szálbiztos sorba teszi, ahonnan a GUI olvassa."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            gui_queue.put_nowait((record.levelno, msg))
        except queue.Full:
            pass  # A felület lemaradt - a fájlnapló így is teljes marad.
        except Exception:
            pass


def setup_logging(level: int = logging.DEBUG) -> str:
    """Naplózás beállítása. Visszaadja a naplófájl útvonalát."""
    global _log_path, _configured
    if _configured and _log_path:
        return _log_path

    _log_path = os.path.join(data_dir(), LOG_FILENAME)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    # Forgatott fájlnapló: 3 MB-onként vált, 5 régebbi példányt tart meg.
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            _log_path, maxBytes=3 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
        root.addHandler(file_handler)
    except Exception as exc:  # írásvédett mappa - ne dőljön el az indulás
        print(f"FIGYELEM: a naplófájl nem hozható létre: {exc}", file=sys.stderr)

    gui_handler = _GuiQueueHandler()
    gui_handler.setLevel(level)
    gui_handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s", "%H:%M:%S"))
    root.addHandler(gui_handler)

    if not getattr(sys, "frozen", False):
        # A Windows konzol alapból cp1252: enélkül minden magyar ő/ű elszállna.
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
        root.addHandler(console)

    _configured = True
    _log_banner()
    return _log_path


def _log_banner() -> None:
    log = logging.getLogger("indulas")
    log.info("=" * 78)
    log.info("Magyar Felirat Fordító indul")
    log.info("Python %s | %s %s", sys.version.split()[0], platform.system(), platform.release())
    log.info("Gép: %s | Program mappa: %s", platform.machine(), app_dir())
    log.info("Adatmappa: %s", data_dir())
    log.info("Naplófájl: %s", _log_path)
    if util.migrated_files:
        log.info("Az exe mellől átköltöztetve: %s", ", ".join(util.migrated_files))
    log.info("=" * 78)


def log_path() -> str:
    return _log_path or os.path.join(data_dir(), LOG_FILENAME)


def set_gui_level(level: int) -> None:
    """A felületen látszó napló részletességének állítása (a fájl mindig DEBUG marad)."""
    for handler in logging.getLogger().handlers:
        if isinstance(handler, _GuiQueueHandler):
            handler.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
