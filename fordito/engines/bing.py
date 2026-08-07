"""Microsoft (Bing) fordítómotor - kulcs és regisztráció nélkül.

Ez a program elsődleges motorja. A Microsoft fordítója mögött ma már nyelvi
modell dolgozik (a válasz maga jelzi: usedLLM), így érezhetően jobb magyar
mondatokat ad, mint a Google - és semmit nem kell hozzá telepíteni.

Hozzáférés: ugyanúgy, ahogy a böngésző teszi a bing.com/translator oldalon.
A munkamenet-jegy egy óráig érvényes, ezt magunk kérjük le és frissítjük.
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from ..logsetup import get_logger
from .base import Engine, EngineError, EngineStatus, ProgressCallback

log = get_logger("motor.bing")

PAGE_URL = "https://www.bing.com/translator"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0")
MAX_CHARS = 950            # a végpont kérésenkénti korlátja
SESSION_MAX_AGE = 50 * 60  # a jegy 60 percig él, 50 percnél cserélünk

_IG = re.compile(r'IG:"([^"]+)"')
_IID = re.compile(r'data-iid="([^"]+)"')
_PARAMS = re.compile(r"params_AbusePreventionHelper\s*=\s*\[([^\]]+)\]")

# A Bing néhány nyelvet más kóddal ismer, mint a feliratsávok.
LANG_FIX = {"zh": "zh-Hans", "pt": "pt-pt", "sr": "sr-Cyrl", "auto": "auto-detect", "": "auto-detect"}


class _Session:
    """Munkamenet: sütik, jegy és azonosítók, óránként megújítva."""

    def __init__(self) -> None:
        self.opener = None
        self.ig = ""
        self.iid = ""
        self.key = ""
        self.token = ""
        self.created = 0.0

    @property
    def expired(self) -> bool:
        return not self.token or (time.time() - self.created) > SESSION_MAX_AGE

    def refresh(self) -> None:
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        opener.addheaders = [
            ("User-Agent", USER_AGENT),
            ("Accept-Language", "hu-HU,hu;q=0.9,en;q=0.8"),
        ]
        html = opener.open(PAGE_URL, timeout=25).read().decode("utf-8", "replace")

        ig = _IG.search(html)
        params = _PARAMS.search(html)
        if not ig or not params:
            raise EngineError("A Microsoft fordító oldala nem a várt formában válaszolt.")

        parts = params.group(1).split(",")
        if len(parts) < 2:
            raise EngineError("Nem sikerült megszerezni a munkamenet jegyét.")

        iid = _IID.search(html)
        self.opener = opener
        self.ig = ig.group(1)
        self.iid = iid.group(1) if iid else "translator.5028"
        self.key = parts[0].strip()
        self.token = parts[1].strip().strip('"')
        self.created = time.time()
        log.debug("Microsoft munkamenet megújítva (IG=%s).", self.ig)


class BingEngine(Engine):
    name = "bing"
    display_name = "Microsoft fordító (legjobb minőség)"
    quality_rank = 60

    def __init__(self, settings):
        super().__init__(settings)
        self.workers = max(1, min(int(getattr(settings, "bing_workers", 8)), 16))
        self._session = _Session()
        self._lock = threading.Lock()
        self._backoff_until = 0.0

    # ------------------------------------------------------------------
    def _ensure_session(self, force: bool = False) -> _Session:
        with self._lock:
            if force or self._session.expired:
                self._session.refresh()
            return self._session

    def status(self) -> EngineStatus:
        try:
            session = self._ensure_session()
            result = self._call(session, "Good morning.", "en")
            if result:
                return EngineStatus(True, "Elérhető",
                                    "Nyelvi modell alapú fordítás, fiók és API kulcs nélkül.")
            return EngineStatus(False, "Üres választ ad", "A Microsoft végpont nem fordított.")
        except Exception as exc:
            return EngineStatus(False, "Nem érhető el", str(exc))

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

        try:
            self._ensure_session()
        except Exception as exc:
            raise EngineError(f"Nem sikerült kapcsolódni a Microsoft fordítóhoz: {exc}") from exc

        log.info("Microsoft fordítás indul: %d egyedi sor, %d szálon.", total, self.workers)
        done = 0
        counter_lock = threading.Lock()

        def work(position: int) -> tuple[int, str]:
            if self._cancelled(cancel):
                return position, ""
            try:
                return position, self._translate_one(texts[position], source_lang, cancel)
            except Exception as exc:
                log.warning("Sor fordítása sikertelen (%d.): %s | %.60s",
                            position, exc, texts[position])
                return position, ""

        with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="bing") as pool:
            futures = [pool.submit(work, i) for i in range(total)]
            for future in as_completed(futures):
                position, translated = future.result()
                results[position] = translated
                with counter_lock:
                    done += 1
                    if progress and (done % 5 == 0 or done == total):
                        progress(done, total)

        failed = sum(1 for r in results if not r)
        if failed:
            log.warning("Microsoft: %d/%d sor nem sikerült, ezekre tartalék jön.", failed, total)
        else:
            log.info("Microsoft fordítás kész: mind a %d sor megvan.", total)
        return results

    # ------------------------------------------------------------------
    def _translate_one(self, text: str, source_lang: str, cancel: Optional[threading.Event]) -> str:
        text = text.strip()
        if not text:
            return ""
        if len(text) > MAX_CHARS:
            # Nagyon hosszú felirat: mondathatáron daraboljuk.
            parts = _split_long(text, MAX_CHARS)
            return " ".join(self._with_retry(p, source_lang, cancel) for p in parts)
        return self._with_retry(text, source_lang, cancel)

    def _with_retry(self, text: str, source_lang: str, cancel: Optional[threading.Event]) -> str:
        last_error: Exception | None = None
        for attempt in range(4):
            if self._cancelled(cancel):
                return ""
            wait = self._backoff_until - time.monotonic()
            if wait > 0:
                time.sleep(min(wait, 10))
            try:
                # A 2. próbálkozástól kikényszerítjük az új munkamenetet:
                # a leggyakoribb hiba a lejárt jegy.
                session = self._ensure_session(force=attempt > 0)
                return self._call(session, text, source_lang)
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code in (429, 503):
                    delay = min(2 ** attempt + 1, 15)
                    self._backoff_until = max(self._backoff_until, time.monotonic() + delay)
                    log.debug("Microsoft korlátozás (HTTP %s), várakozás %.0f mp.", exc.code, delay)
                    time.sleep(delay)
                else:
                    time.sleep(0.5 * (attempt + 1))
            except (EngineError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                time.sleep(0.5 * (attempt + 1))
        raise EngineError(f"4 próbálkozás után sem sikerült: {last_error}")

    def _call(self, session: _Session, text: str, source_lang: str) -> str:
        code = (source_lang or "auto").lower()
        from_lang = LANG_FIX.get(code, code)

        data = urllib.parse.urlencode({
            "fromLang": from_lang,
            "to": "hu",
            "text": text,
            "token": session.token,
            "key": session.key,
        }).encode("utf-8")

        url = f"https://www.bing.com/ttranslatev3?isVertical=1&IG={session.ig}&IID={session.iid}"
        request = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
            "Referer": PAGE_URL,
        })
        assert session.opener is not None
        with session.opener.open(request, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))

        # Hiba esetén a végpont szótárral válaszol, nem listával.
        if isinstance(payload, dict):
            raise EngineError(f"A Microsoft végpont hibát adott: {payload.get('statusCode', payload)}")
        if not payload or "translations" not in payload[0]:
            raise EngineError("Váratlan válaszszerkezet a Microsoft végponttól.")
        return payload[0]["translations"][0]["text"].strip()


def _split_long(text: str, limit: int) -> list[str]:
    """Hosszú szöveg darabolása mondathatáron, hogy beleférjen egy kérésbe."""
    parts: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?。！？])\s+", text):
        if len(current) + len(sentence) + 1 > limit and current:
            parts.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current.strip())
    return parts or [text[:limit]]
