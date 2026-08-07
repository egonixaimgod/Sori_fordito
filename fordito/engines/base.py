"""Fordítómotorok közös felülete."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional


class EngineError(Exception):
    """Fordítómotor hibája."""


@dataclass
class EngineStatus:
    available: bool
    message: str
    detail: str = ""


# Előrehaladás visszajelzés: (kész_darab, összes_darab)
ProgressCallback = Callable[[int, int], None]


class Engine(ABC):
    """Egy fordítómotor. Mindig magyarra fordít."""

    name: str = "alap"
    display_name: str = "Alap"
    # Minőségi rangsor: nagyobb szám = jobb fordítás várható.
    quality_rank: int = 0

    def __init__(self, settings):
        self.settings = settings

    @abstractmethod
    def status(self) -> EngineStatus:
        """Használható-e a motor most, és ha nem, miért nem."""

    @abstractmethod
    def translate(
        self,
        texts: list[str],
        source_lang: str,
        progress: Optional[ProgressCallback] = None,
        cancel: Optional[threading.Event] = None,
    ) -> list[str]:
        """Szövegek fordítása magyarra.

        A visszaadott lista MINDIG ugyanolyan hosszú, mint a bemenet, és
        pozíciónként megfelel neki. Ami nem sikerült, ott üres sztring áll.
        """

    def warmup(self) -> None:
        """Opcionális előmelegítés (pl. modell betöltése a memóriába)."""

    def describe(self) -> str:
        return self.display_name

    @staticmethod
    def _cancelled(cancel: Optional[threading.Event]) -> bool:
        return cancel is not None and cancel.is_set()
