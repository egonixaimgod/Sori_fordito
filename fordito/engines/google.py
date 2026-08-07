"""Google Translate motor - regisztráció és kulcs nélkül, párhuzamosan.

A régi verzió legsúlyosabb hibája itt szűnik meg: 50 sort fűzött össze
sortöréssel, majd a választ sortörésnél vágta szét. A Google viszont
összevonja és szétszedi a sorokat, így a darabszám elcsúszott, és onnantól
KÉT-HÁROM PERCNYI felirat rossz idővonalra került vagy kiürült.

Itt minden feliratsor külön kérésben megy: a hozzárendelés matematikailag
sem tud elcsúszni. A sebességet a párhuzamosság és a gyorsítótár adja vissza.
"""

from __future__ import annotations

import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from ..logsetup import get_logger
from .base import Engine, EngineError, EngineStatus, ProgressCallback

log = get_logger("motor.google")

ENDPOINT = "https://translate.googleapis.com/translate_a/single"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
MAX_CHARS = 1800  # a végpont hossza korlátos


class GoogleEngine(Engine):
    name = "google"
    display_name = "Google Translate (ingyenes, gyors)"
    quality_rank = 10

    def __init__(self, settings):
        super().__init__(settings)
        self.workers = max(1, min(int(getattr(settings, "google_workers", 8)), 16))
        self._throttle = threading.Lock()
        self._backoff_until = 0.0

    # ------------------------------------------------------------------
    def status(self) -> EngineStatus:
        try:
            result = self._request("test", "en")
            if result:
                return EngineStatus(True, "Elérhető", "Nem kell hozzá fiók és API kulcs.")
            return EngineStatus(False, "Nem válaszol", "A Google végpont üres választ adott.")
        except Exception as exc:
            return EngineStatus(False, "Nem érhető el", f"Hálózati hiba: {exc}")

    # ------------------------------------------------------------------
    def translate(
        self,
        texts: list[str],
        source_lang: str,
        progress: Optional[ProgressCallback] = None,
        cancel: Optional[threading.Event] = None,
    ) -> list[str]:
        total = len(texts)
        results: list[str] = [""] * total
        if total == 0:
            return results

        done = 0
        lock = threading.Lock()
        log.info("Google fordítás indul: %d egyedi sor, %d szálon.", total, self.workers)

        def work(position: int) -> tuple[int, str]:
            if self._cancelled(cancel):
                return position, ""
            try:
                return position, self._translate_one(texts[position], source_lang, cancel)
            except Exception as exc:
                log.warning("Sor fordítása sikertelen (%d.): %s | szöveg: %.60s",
                            position, exc, texts[position])
                return position, ""

        with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="google") as pool:
            futures = [pool.submit(work, i) for i in range(total)]
            for future in as_completed(futures):
                position, translated = future.result()
                results[position] = translated
                with lock:
                    done += 1
                    if progress and (done % 5 == 0 or done == total):
                        progress(done, total)

        failed = sum(1 for r in results if not r)
        if failed:
            log.warning("Google: %d/%d sor nem sikerült.", failed, total)
        else:
            log.info("Google fordítás kész: mind a %d sor megvan.", total)
        return results

    # ------------------------------------------------------------------
    def _translate_one(self, text: str, source_lang: str, cancel: Optional[threading.Event]) -> str:
        text = text.strip()
        if not text:
            return ""
        # Túl hosszú szöveg: mondatonként daraboljuk, majd összefűzzük.
        if len(text) > MAX_CHARS:
            parts = _split_long(text, MAX_CHARS)
            return " ".join(self._request_with_retry(p, source_lang, cancel) for p in parts)
        return self._request_with_retry(text, source_lang, cancel)

    def _request_with_retry(self, text: str, source_lang: str, cancel: Optional[threading.Event]) -> str:
        last_error: Exception | None = None
        for attempt in range(4):
            if self._cancelled(cancel):
                return ""
            # Ha nemrég korlátozott a szolgáltatás, mindenki vár egy kicsit.
            wait = self._backoff_until - time.monotonic()
            if wait > 0:
                time.sleep(min(wait, 10))
            try:
                return self._request(text, source_lang)
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code in (429, 503):
                    delay = min(2 ** attempt + random.uniform(0, 1.5), 20)
                    with self._throttle:
                        self._backoff_until = max(self._backoff_until, time.monotonic() + delay)
                    log.debug("Google korlátozás (HTTP %s), várakozás %.1f mp.", exc.code, delay)
                    time.sleep(delay)
                    continue
                if exc.code >= 500:
                    time.sleep(1 + attempt)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                time.sleep(0.6 * (attempt + 1))
        raise EngineError(f"4 próbálkozás után sem sikerült: {last_error}")

    @staticmethod
    def _request(text: str, source_lang: str) -> str:
        params = urllib.parse.urlencode({
            "client": "gtx",
            "sl": source_lang if source_lang and source_lang != "auto" else "auto",
            "tl": "hu",
            "dt": "t",
            "q": text,
        })
        request = urllib.request.Request(f"{ENDPOINT}?{params}", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        # A válasz első eleme a mondatdarabok listája - ezeket fűzzük össze.
        if not payload or not payload[0]:
            return ""
        return "".join(part[0] for part in payload[0] if part and part[0]).strip()


def _split_long(text: str, limit: int) -> list[str]:
    """Nagyon hosszú szöveg darabolása mondathatáron."""
    parts: list[str] = []
    current = ""
    for sentence in text.replace("! ", "!\x00").replace("? ", "?\x00").replace(". ", ".\x00").split("\x00"):
        if len(current) + len(sentence) + 1 > limit and current:
            parts.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current.strip())
    return parts or [text[:limit]]
