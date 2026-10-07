"""A teljes munkafolyamat: elemzés -> kinyerés -> fordítás -> külön felirat / visszaírás az MKV-be.

Egy fájl és egy egész mappa feldolgozása ugyanazon az úton megy végig, így
kötegelt módban is pontosan az történik, mint egyetlen fájlnál.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from .config import TRACK_TITLE
from .logsetup import get_logger
from .media import (FFmpeg, MediaError, MediaInfo, SubtitleTrack,
                    find_matching_track, pick_best_track)
from .subtitles import load_document
from .translator import TranslationStats, Translator
from .util import Stopwatch, human_time, human_size

log = get_logger("folyamat")

VIDEO_EXTENSIONS = (".mkv", ".mp4", ".m4v", ".mov", ".avi", ".webm", ".wmv",
                    ".ts", ".m2ts", ".mpg", ".mpeg", ".flv")
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa")
# Csak az MKV-ba írunk vissza (sáv be/ki). A többi konténer (MP4, AVI, ...) nem
# tud rendesen SRT/ASS sávot tárolni, ezért azoknál a videó érintetlen marad, és
# a magyar felirat külön fájlba kerül mellé - ezt minden lejátszó megjeleníti.
MUXABLE_EXTENSIONS = (".mkv",)

# A videó mellé kerülő magyar feliratfájl neve: <videó neve>_magyar_felirat.srt
SUBTITLE_SUFFIX = "_magyar_felirat"

# Külső feliratfájl nyelvjelölései a fájlnévben (film.en.srt, film.eng.srt ...).
_SIDECAR_PREFERRED = ("", "en", "eng", "english", "angol")
_SIDECAR_HUNGARIAN = ("hu", "hun", "hungarian", "magyar")


def find_sidecar_subtitle(video_path: str) -> Optional[str]:
    """A videó melletti, azonos nevű feliratfájl (film.srt, film.en.srt, ...).

    MP4-nél gyakori, hogy a felirat nincs beágyazva, csak ott van mellette.
    A saját kimenetünket (_magyar_felirat) és a már magyar fájlokat kihagyjuk;
    több jelölt közül a jelöletlen vagy angol az első."""
    folder = os.path.dirname(video_path) or "."
    base = os.path.splitext(os.path.basename(video_path))[0]
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    candidates: list[tuple[int, str]] = []
    for name in names:
        stem, extension = os.path.splitext(name)
        if extension.lower() not in SUBTITLE_EXTENSIONS or SUBTITLE_SUFFIX in stem:
            continue
        if stem != base and not stem.startswith(base + "."):
            continue
        tag = stem[len(base) + 1:].lower() if stem != base else ""
        if any(part in _SIDECAR_HUNGARIAN for part in tag.split(".")):
            continue
        rank = _SIDECAR_PREFERRED.index(tag) if tag in _SIDECAR_PREFERRED else len(_SIDECAR_PREFERRED)
        candidates.append((rank, os.path.join(folder, name)))
    return min(candidates)[1] if candidates else None

# A folyamat szakaszai és súlyuk a haladásjelzőn.
STAGE_EXTRACT = 0.05
STAGE_TRANSLATE = 0.80
STAGE_MUX = 0.15


class JobStatus(Enum):
    OK = "kész"
    SKIPPED = "kihagyva"
    ERROR = "hiba"
    CANCELLED = "megszakítva"


@dataclass
class JobResult:
    path: str
    status: JobStatus = JobStatus.ERROR
    message: str = ""
    subtitle_path: str = ""
    video_path: str = ""
    stats: Optional[TranslationStats] = None
    seconds: float = 0.0

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


@dataclass
class Callbacks:
    """A felület felé menő visszajelzések. Mind opcionális."""
    on_stage: Callable[[str], None] = lambda text: None
    on_progress: Callable[[float], None] = lambda fraction: None
    on_file_start: Callable[[int, int, str], None] = lambda index, total, path: None
    on_file_done: Callable[[JobResult], None] = lambda result: None
    on_queue: Callable[[list[str]], None] = lambda paths: None


class Pipeline:
    def __init__(self, settings, ffmpeg: Optional[FFmpeg] = None,
                 translator: Optional[Translator] = None):
        self.settings = settings
        self.ffmpeg = ffmpeg or FFmpeg()
        self.translator = translator or Translator(settings)

    # ==================================================================
    #  Mappa bejárása
    # ==================================================================
    def scan_folder(self, folder: str) -> list[str]:
        """Videofájlok összegyűjtése a mappából (kérésre az almappákból is)."""
        recursive = bool(getattr(self.settings, "recursive_scan", True))
        found: list[str] = []
        if recursive:
            for root, _dirs, files in os.walk(folder):
                for name in sorted(files):
                    if name.lower().endswith(VIDEO_EXTENSIONS):
                        found.append(os.path.join(root, name))
        else:
            for name in sorted(os.listdir(folder)):
                full = os.path.join(folder, name)
                if os.path.isfile(full) and name.lower().endswith(VIDEO_EXTENSIONS):
                    found.append(full)
        total_size = sum(os.path.getsize(p) for p in found if os.path.exists(p))
        log.info(
            "Mappa átnézve: %s -> %d videófájl (%s), almappákkal: %s",
            folder, len(found), human_size(total_size), "igen" if recursive else "nem",
        )
        return found

    # ==================================================================
    #  Kötegelt feldolgozás
    # ==================================================================
    def process_many(
        self,
        paths: list[str],
        callbacks: Callbacks,
        cancel: Optional[threading.Event] = None,
        preference: Optional[SubtitleTrack] = None,
    ) -> list[JobResult]:
        results: list[JobResult] = []
        total = len(paths)
        watch = Stopwatch()
        log.info("=" * 70)
        log.info("KÖTEGELT FELDOLGOZÁS INDUL: %d fájl", total)
        if preference:
            log.info("Kért forrássáv minden fájlban: %s", preference.label)
        else:
            log.info("Forrássáv: fájlonként automatikusan a legjobb")
        log.info("=" * 70)

        for index, path in enumerate(paths, start=1):
            if cancel and cancel.is_set():
                log.info("A feldolgozást megszakították a %d. fájlnál.", index)
                results.append(JobResult(path, JobStatus.CANCELLED, "Megszakítva"))
                break
            callbacks.on_file_start(index, total, path)
            # Soha nem hagyunk ki semmit: ami idáig eljut, azt a felhasználó
            # kipipálta a munkalistán. A már kész fájlok kiszűrése a listánál
            # történik, nem itt - így mindig látszik, mi miért marad ki.
            result = self.process_file(path, callbacks=callbacks, cancel=cancel,
                                       preference=preference)
            results.append(result)
            callbacks.on_file_done(result)

        ok = sum(1 for r in results if r.status is JobStatus.OK)
        skipped = sum(1 for r in results if r.status is JobStatus.SKIPPED)
        failed = sum(1 for r in results if r.status is JobStatus.ERROR)
        log.info("=" * 70)
        log.info(
            "KÖTEG KÉSZ: %d sikeres, %d kihagyott, %d hibás | összesen %s",
            ok, skipped, failed, human_time(watch.elapsed()),
        )
        log.info("=" * 70)
        return results

    # ==================================================================
    #  Egy fájl feldolgozása
    # ==================================================================
    def process_file(
        self,
        path: str,
        track: Optional[SubtitleTrack] = None,
        callbacks: Optional[Callbacks] = None,
        cancel: Optional[threading.Event] = None,
        preference: Optional[SubtitleTrack] = None,
    ) -> JobResult:
        callbacks = callbacks or Callbacks()
        result = JobResult(path=path)
        watch = Stopwatch()
        log.info("-" * 70)
        log.info("FÁJL: %s", path)

        if not os.path.exists(path):
            result.message = "A fájl nem található."
            log.error(result.message)
            return result

        # Külön feliratfájl: csak fordítás, nincs mit muxolni.
        if path.lower().endswith(SUBTITLE_EXTENSIONS):
            return self._process_subtitle_file(path, callbacks, cancel, watch)

        temp_dir = tempfile.mkdtemp(prefix="fordito_")
        try:
            # --- 1. Sávok elemzése -------------------------------------
            callbacks.on_stage("Felirat sávok elemzése...")
            callbacks.on_progress(0.01)
            info = self.ffmpeg.probe(path)

            # Nincs (szöveges) beágyazott felirat: a videó melletti feliratfájlt fordítjuk.
            if track is None and not info.text_tracks:
                sidecar = find_sidecar_subtitle(path)
                if sidecar:
                    log.info("Nincs beágyazott szöveges felirat - a mellette lévő "
                             "feliratfájlt fordítjuk: %s", os.path.basename(sidecar))
                    extension = os.path.splitext(sidecar)[1]
                    output = f"{os.path.splitext(path)[0]}{SUBTITLE_SUFFIX}{extension}"
                    sidecar_result = self._process_subtitle_file(
                        sidecar, callbacks, cancel, watch, output_path=output)
                    sidecar_result.path = path
                    return sidecar_result

            if not info.subtitle_tracks:
                result.status = JobStatus.SKIPPED
                result.message = "Nincs benne felirat sáv, és mellette sincs feliratfájl."
                log.warning("%s: %s", result.name, result.message)
                return result

            # Itt SOHA nem hagyunk ki fájlt. Ami idáig eljut, azt a felhasználó
            # kipipálta a munkalistán - a döntés az övé, nem a programé.
            # A már kész fájlok jelölése a listában történik, láthatóan.
            if info.has_hungarian:
                log.info("Van már benne magyar sáv, de ki van pipálva - "
                         "újrafordítjuk, a régi magyar sáv lecserélődik.")

            # Sávválasztás: kézzel megadott > kötegelt minta > automatikus.
            chosen = track
            if chosen is None and preference is not None:
                chosen = find_matching_track(info.subtitle_tracks, preference)
                if chosen:
                    log.info("A kért sáv megvan ebben a fájlban is: %s", chosen.label)
                else:
                    log.warning("A kért sáv (%s) nincs meg ebben a fájlban - "
                                "automatikus választás lép életbe.", preference.label)
            if chosen is None:
                chosen = pick_best_track(info.subtitle_tracks)
            if not chosen:
                result.status = JobStatus.SKIPPED
                bitmap = all(t.is_bitmap for t in info.subtitle_tracks)
                result.message = (
                    "Csak képalapú felirat van benne (OCR kellene hozzá)."
                    if bitmap else "Nincs használható forrássáv."
                )
                log.warning("%s: %s", result.name, result.message)
                return result

            log.info("Forrássáv: %s", chosen.label)
            if cancel and cancel.is_set():
                result.status = JobStatus.CANCELLED
                return result

            # --- 2. Felirat kinyerése ----------------------------------
            callbacks.on_stage(f"Felirat kinyerése ({(chosen.language or '??').upper()})...")
            extracted = os.path.join(temp_dir, f"forras{chosen.extension}")
            extracted = self.ffmpeg.extract(path, chosen, extracted)
            callbacks.on_progress(STAGE_EXTRACT)

            # --- 3. Fordítás -------------------------------------------
            document = load_document(extracted)
            if not document.get_texts():
                result.status = JobStatus.SKIPPED
                result.message = "A kinyert felirat üres."
                log.warning("%s: %s", result.name, result.message)
                return result

            def translate_progress(done: int, total: int, phase: str) -> None:
                callbacks.on_stage(f"{phase}  {done}/{total}")
                if total:
                    callbacks.on_progress(STAGE_EXTRACT + STAGE_TRANSLATE * (done / total))

            stats = self.translator.translate_document(
                document, chosen.language, translate_progress, cancel
            )
            result.stats = stats
            if cancel and cancel.is_set():
                result.status = JobStatus.CANCELLED
                result.message = "Megszakítva fordítás közben."
                return result

            # --- 4. Magyar feliratfájl mentése -------------------------
            extension = os.path.splitext(extracted)[1]
            base = os.path.splitext(path)[0]
            subtitle_path = f"{base}{SUBTITLE_SUFFIX}{extension}"
            document.save(subtitle_path)
            result.subtitle_path = subtitle_path
            callbacks.on_progress(STAGE_EXTRACT + STAGE_TRANSLATE)

            # --- 5. Az MKV kezelése ------------------------------------
            is_mkv = path.lower().endswith(MUXABLE_EXTENSIONS)
            if not is_mkv:
                # MP4, AVI, ...: a videóhoz nem nyúlunk, a felirat külön fájlban van.
                result.video_path = path
                log.info("Nem MKV - a videó változatlan, a magyar felirat külön fájlban: %s",
                         os.path.basename(subtitle_path))
            elif getattr(self.settings, "mux_into_mkv", False):
                # A felirat bekerül az MKV-ba, alapértelmezett sávként.
                self._mux(path, subtitle_path, info, callbacks, result)
            elif getattr(self.settings, "remove_hungarian_tracks", True) and info.has_hungarian:
                # Nem muxolunk, de a régi magyar sávok kitakarodnak a fájlból,
                # hogy egyetlen magyar felirat legyen: a külön .srt.
                self._strip(path, info, callbacks, result)
            else:
                result.video_path = path

            # --- 6. A külön feliratfájl sorsa --------------------------
            # Ha a felirat bekerült az MKV-be, a külön .srt csak zavart okoz:
            # a lejátszók (MPC, VLC) a külső fájlt részesítik előnyben, ezért
            # két magyar felirat jelenik meg a listában, és nem az kerül elő,
            # amit alapértelmezettnek jelöltünk. Ezért alapból töröljük.
            muxed = is_mkv and getattr(self.settings, "mux_into_mkv", False) and bool(result.video_path)
            if muxed and not getattr(self.settings, "keep_srt_file", False):
                try:
                    os.remove(subtitle_path)
                    result.subtitle_path = ""
                    log.info("A külön feliratfájl törölve - a felirat az MKV-ban van.")
                except OSError as exc:
                    log.debug("A feliratfájl törlése nem sikerült: %s", exc)

            callbacks.on_progress(1.0)
            result.status = JobStatus.OK
            result.message = stats.summary()
            log.info("KÉSZ: %s (%s)", result.name, human_time(watch.elapsed()))

        except MediaError as exc:
            result.status = JobStatus.ERROR
            result.message = str(exc)
            log.error("Média hiba (%s): %s", result.name, exc)
        except Exception as exc:  # noqa: BLE001 - a köteg soha ne álljon meg egy fájl miatt
            result.status = JobStatus.ERROR
            result.message = f"Váratlan hiba: {exc}"
            log.exception("Váratlan hiba a(z) %s feldolgozásakor", result.name)
        finally:
            result.seconds = watch.elapsed()
            shutil.rmtree(temp_dir, ignore_errors=True)

        return result

    # ------------------------------------------------------------------
    def _mux(self, path: str, subtitle_path: str, info: MediaInfo,
             callbacks: Callbacks, result: JobResult) -> None:
        """A magyar feliratot beleteszi az MKV-be alapértelmezett sávként."""
        callbacks.on_stage(f"„{TRACK_TITLE}” beillesztése az MKV-be...")
        folder = os.path.dirname(path) or "."
        base = os.path.splitext(os.path.basename(path))[0]
        temp_output = os.path.join(folder, f".{base}.fordito.tmp.mkv")

        def mux_progress(fraction: float) -> None:
            callbacks.on_progress(STAGE_EXTRACT + STAGE_TRANSLATE + STAGE_MUX * fraction)

        try:
            self.ffmpeg.mux_subtitle(
                path, subtitle_path, info, temp_output,
                set_default=bool(getattr(self.settings, "set_as_default", True)),
                progress_cb=mux_progress,
            )

            callbacks.on_stage("Az elkészült fájl ellenőrzése...")
            if not self.ffmpeg.verify_muxed(temp_output, info.duration):
                raise MediaError("Az elkészült MKV nem ment át az ellenőrzésen.")

            if getattr(self.settings, "replace_original", True):
                if getattr(self.settings, "keep_backup", False):
                    backup = path + ".bak"
                    if os.path.exists(backup):
                        os.remove(backup)
                    os.replace(path, backup)
                    log.info("Biztonsági másolat: %s", os.path.basename(backup))
                os.replace(temp_output, path)
                result.video_path = path
                log.info("Az eredeti MKV lecserélve a magyar sávval bővített fájlra.")
            else:
                final = os.path.join(folder, f"{base}{SUBTITLE_SUFFIX}.mkv")
                os.replace(temp_output, final)
                result.video_path = final
                log.info("Új fájl készült: %s", os.path.basename(final))

        except Exception:
            if os.path.exists(temp_output):
                try:
                    os.remove(temp_output)
                    log.debug("Félkész ideiglenes fájl törölve.")
                except OSError:
                    pass
            raise

    # ------------------------------------------------------------------
    def _strip(self, path: str, info: MediaInfo, callbacks: Callbacks,
               result: JobResult) -> None:
        """A régi magyar sávok kiszedése az MKV-ból (új sávot nem teszünk be)."""
        callbacks.on_stage("Régi magyar sávok eltávolítása az MKV-ból...")
        folder = os.path.dirname(path) or "."
        base = os.path.splitext(os.path.basename(path))[0]
        temp_output = os.path.join(folder, f".{base}.fordito.tmp.mkv")

        def strip_progress(fraction: float) -> None:
            callbacks.on_progress(STAGE_EXTRACT + STAGE_TRANSLATE + STAGE_MUX * fraction)

        try:
            self.ffmpeg.strip_hungarian(path, info, temp_output, progress_cb=strip_progress)

            callbacks.on_stage("Az elkészült fájl ellenőrzése...")
            if not self.ffmpeg.verify_stripped(temp_output, info.duration):
                raise MediaError("Az elkészült MKV nem ment át az ellenőrzésen.")

            if getattr(self.settings, "keep_backup", False):
                backup = path + ".bak"
                if os.path.exists(backup):
                    os.remove(backup)
                os.replace(path, backup)
                log.info("Biztonsági másolat: %s", os.path.basename(backup))
            os.replace(temp_output, path)
            result.video_path = path
            log.info("A magyar sávok eltávolítva - a felirat a külön .srt fájlban van.")

        except Exception:
            if os.path.exists(temp_output):
                try:
                    os.remove(temp_output)
                except OSError:
                    pass
            raise

    # ------------------------------------------------------------------
    def _process_subtitle_file(self, path: str, callbacks: Callbacks,
                               cancel: Optional[threading.Event], watch: Stopwatch,
                               output_path: str = "") -> JobResult:
        """Külön feliratfájl (SRT/ASS) fordítása - videó nélkül.

        Az `output_path` akkor kell, ha a fájl egy videó melletti felirat: a
        kimenet ilyenkor a VIDEÓ nevét kapja (film_magyar_felirat.srt)."""
        result = JobResult(path=path)
        try:
            callbacks.on_stage("Feliratfájl beolvasása...")
            document = load_document(path)

            def translate_progress(done: int, total: int, phase: str) -> None:
                callbacks.on_stage(f"{phase}  {done}/{total}")
                if total:
                    callbacks.on_progress(done / total)

            stats = self.translator.translate_document(document, "", translate_progress, cancel)
            result.stats = stats

            if cancel and cancel.is_set():
                result.status = JobStatus.CANCELLED
                return result

            base, extension = os.path.splitext(path)
            output = output_path or f"{base}{SUBTITLE_SUFFIX}{extension}"
            document.save(output)
            result.subtitle_path = output
            result.status = JobStatus.OK
            result.message = stats.summary()
            callbacks.on_progress(1.0)
            log.info("KÉSZ: %s", os.path.basename(output))
        except Exception as exc:  # noqa: BLE001
            result.status = JobStatus.ERROR
            result.message = f"Hiba: {exc}"
            log.exception("Hiba a feliratfájl fordításakor")
        finally:
            result.seconds = watch.elapsed()
        return result
