"""Fordításgyorsítótár SQLite-ban.

Egy anime évadban több száz sor ismétlődik (visszatérő megszólítások, OP/ED
szövegek, epizódcímek). Ezeket egyszer fordítjuk le, utána azonnal jönnek.
A kétszer indított munka gyakorlatilag ingyenessé válik.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading

from .logsetup import get_logger
from .util import data_dir

log = get_logger("gyorsitotar")

CACHE_FILENAME = "fordito_cache.sqlite"


class TranslationCache:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.path = os.path.join(data_dir(), CACHE_FILENAME)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self.hits = 0
        self.misses = 0
        if enabled:
            self._connect()

    def _connect(self) -> None:
        try:
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS translations (
                       key TEXT PRIMARY KEY,
                       source TEXT NOT NULL,
                       target TEXT NOT NULL,
                       engine TEXT NOT NULL,
                       created REAL DEFAULT (strftime('%s','now'))
                   )"""
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()
            count = self._conn.execute("SELECT COUNT(*) FROM translations").fetchone()[0]
            log.info("Gyorsítótár megnyitva: %s (%d tárolt fordítás)", self.path, count)
        except Exception as exc:
            log.warning("A gyorsítótár nem nyitható meg (%s) - cache nélkül folytatjuk.", exc)
            self.enabled = False
            self._conn = None

    @staticmethod
    def _key(text: str, engine: str, model: str, source_lang: str) -> str:
        payload = f"{engine}|{model}|{source_lang}|hu|{text}".encode("utf-8")
        return hashlib.sha1(payload).hexdigest()

    def get(self, text: str, engine: str, model: str, source_lang: str) -> str | None:
        if not self.enabled or not self._conn:
            return None
        key = self._key(text, engine, model, source_lang)
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT target FROM translations WHERE key = ?", (key,)
                ).fetchone()
        except Exception as exc:
            log.debug("Gyorsítótár olvasási hiba: %s", exc)
            return None
        if row:
            self.hits += 1
            return row[0]
        self.misses += 1
        return None

    def put_many(self, pairs: list[tuple[str, str]], engine: str, model: str, source_lang: str) -> None:
        """Több (eredeti, fordítás) pár mentése egy tranzakcióban."""
        if not self.enabled or not self._conn or not pairs:
            return
        rows = [
            (self._key(src, engine, model, source_lang), src, dst, engine)
            for src, dst in pairs
            if src and dst
        ]
        try:
            with self._lock:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO translations (key, source, target, engine) VALUES (?,?,?,?)",
                    rows,
                )
                self._conn.commit()
            log.debug("%d fordítás elmentve a gyorsítótárba.", len(rows))
        except Exception as exc:
            log.debug("Gyorsítótár írási hiba: %s", exc)

    def stats(self) -> str:
        total = self.hits + self.misses
        ratio = (self.hits / total * 100) if total else 0.0
        return f"találat {self.hits}/{total} ({ratio:.0f}%)"

    def clear(self) -> int:
        if not self.enabled or not self._conn:
            return 0
        with self._lock:
            count = self._conn.execute("SELECT COUNT(*) FROM translations").fetchone()[0]
            self._conn.execute("DELETE FROM translations")
            self._conn.commit()
        log.info("Gyorsítótár ürítve (%d bejegyzés).", count)
        return count

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
