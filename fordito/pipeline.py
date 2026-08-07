"""A teljes munkafolyamat: elemzés -> kinyerés -> fordítás -> visszaírás az MKV-be.

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
from .media import FFmpeg, MediaError, MediaInfo, SubtitleTrack, pick_best_track
from .subtitles import load_document
from .translator import TranslationStats, Translator
from .util import Stopwatch, human_time, human_size

log = get_logger("folyamat")

VIDEO_EXTENSIONS = (".mkv",)
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa")

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
            "Mappa átnézve: %s -> %d MKV fájl (%s), almappákkal: %s",
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
    ) -> list[JobResult]:
        results: list[JobResult] = []
        total = len(paths)
        watch = Stopwatch()
        log.info("=" * 70)
        log.info("KÖTEGELT FELDOLGOZÁS INDUL: %d fájl", total)
        log.info("=" * 70)

        for index, path in enumerate(paths, start=1):
            if cancel and cancel.is_set():
                log.info("A feldolgozást megszakították a %d. fájlnál.", index)
                results.append(JobResult(path, JobStatus.CANCELLED, "Megszakítva"))
                break
            callbacks.on_file_start(index, total, path)
            result = self.process_file(path, callbacks=callbacks, cancel=cancel)
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

            if not info.subtitle_tracks:
                result.status = JobStatus.SKIPPED
                result.message = "Nincs benne felirat sáv."
                log.warning("%s: %s", result.name, result.message)
                return result

            if (getattr(self.settings, "skip_if_hungarian_exists", True)
                    and info.has_hungarian
                    and getattr(self.settings, "mux_into_mkv", True)):
                result.status = JobStatus.SKIPPED
                result.message = "Már van benne magyar felirat."
                log.info("%s: %s - kihagyva.", result.name, result.message)
                return result

            chosen = track or pick_best_track(info.subtitle_tracks)
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
            subtitle_path = f"{base}.hu{extension}"
            document.save(subtitle_path)
            result.subtitle_path = subtitle_path
            callbacks.on_progress(STAGE_EXTRACT + STAGE_TRANSLATE)

            # --- 5. Visszaírás az MKV-be -------------------------------
            if getattr(self.settings, "mux_into_mkv", True):
                self._mux(path, subtitle_path, info, callbacks, result)
            else:
                result.video_path = path

            if not getattr(self.settings, "keep_srt_file", True) and result.video_path != path:
                pass  # a videó nem cserélődött, a feliratot mindenképp megtartjuk
            elif not getattr(self.settings, "keep_srt_file", True):
                try:
                    os.remove(subtitle_path)
                    result.subtitle_path = ""
                    log.debug("A külön feliratfájl törölve (a beállítás szerint).")
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
                final = os.path.join(folder, f"{base}.hu.mkv")
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
    def _process_subtitle_file(self, path: str, callbacks: Callbacks,
                               cancel: Optional[threading.Event], watch: Stopwatch) -> JobResult:
        """Külön feliratfájl (SRT/ASS) fordítása - videó nélkül."""
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
            output = f"{base}.hu{extension}"
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
