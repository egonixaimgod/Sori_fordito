"""MKV kezelés ffmpeg/ffprobe segítségével: sávelemzés, kinyerés, visszamuxolás."""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field

from .config import TARGET_LANG_ISO3, TRACK_TITLE
from .logsetup import get_logger
from .util import popen_process, resource_dir, run_process

log = get_logger("media")

# Képalapú feliratok: ezekből csak OCR-rel lehetne szöveget csinálni.
BITMAP_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"}
# Szöveges feliratok, amiket eredeti formában ki tudunk másolni.
TEXT_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text", "microdvd"}

# Fordítási forrásnak ebben a sorrendben a legjobbak a sávok.
LANG_PREFERENCE = ["eng", "en", "jpn", "ja", "jp"]
# Ezek a sávok jellemzően nem tartalmaznak teljes párbeszédet.
PARTIAL_TRACK_HINTS = ("signs", "songs", "s&s", "forced", "sign/song", "felirat nélkül")
HUNGARIAN_CODES = {"hun", "hu", "magyar", "hungarian"}


class MediaError(Exception):
    """Média művelet hibája, felhasználónak is megmutatható üzenettel."""


@dataclass
class SubtitleTrack:
    index: int                  # abszolút stream index a fájlban
    sub_index: int              # hányadik felirat sáv (0-tól)
    codec: str = ""
    language: str = ""
    title: str = ""
    is_default: bool = False
    is_forced: bool = False
    event_count: int = 0

    @property
    def is_bitmap(self) -> bool:
        return self.codec in BITMAP_CODECS

    @property
    def is_hungarian(self) -> bool:
        return self.language.lower() in HUNGARIAN_CODES

    @property
    def extension(self) -> str:
        return ".ass" if self.codec in ("ass", "ssa") else ".srt"

    @property
    def label(self) -> str:
        parts = [f"#{self.index}", (self.language or "??").upper()]
        if self.title:
            parts.append(self.title)
        parts.append(f"[{self.codec}]")
        flags = []
        if self.is_default:
            flags.append("alap")
        if self.is_forced:
            flags.append("kényszerített")
        if self.is_bitmap:
            flags.append("KÉPALAPÚ")
        if self.event_count:
            flags.append(f"{self.event_count} sor")
        if flags:
            parts.append("(" + ", ".join(flags) + ")")
        return "  ".join(parts)


@dataclass
class MediaInfo:
    path: str
    duration: float = 0.0
    subtitle_tracks: list[SubtitleTrack] = field(default_factory=list)
    all_subtitle_count: int = 0
    dispositions: list[str] = field(default_factory=list)  # felirat sávonként a megtartandó flag-ek

    @property
    def has_hungarian(self) -> bool:
        return any(t.is_hungarian for t in self.subtitle_tracks)

    @property
    def text_tracks(self) -> list[SubtitleTrack]:
        return [t for t in self.subtitle_tracks if not t.is_bitmap]


class FFmpeg:
    """Az ffmpeg/ffprobe bináris köré húzott vékony réteg."""

    def __init__(self) -> None:
        self.ffmpeg = self._locate("ffmpeg")
        self.ffprobe = self._locate("ffprobe")
        log.info("ffmpeg: %s", self.ffmpeg)
        log.info("ffprobe: %s", self.ffprobe)

    @staticmethod
    def _locate(name: str) -> str:
        exe = f"{name}.exe" if os.name == "nt" else name
        # 1. A program mellett (ez a csomagolt eset).
        local = os.path.join(resource_dir(), exe)
        if os.path.exists(local):
            return local
        # 2. Rendszer PATH.
        found = shutil.which(name)
        if found:
            return found
        log.warning("%s nem található sem a program mellett, sem a PATH-ban!", name)
        return name

    def available(self) -> bool:
        try:
            result = run_process([self.ffmpeg, "-version"], timeout=15)
            return result.returncode == 0
        except Exception as exc:
            log.error("Az ffmpeg nem futtatható: %s", exc)
            return False

    # ------------------------------------------------------------------
    def probe(self, path: str) -> MediaInfo:
        """A fájl felirat sávjainak feltérképezése."""
        command = [
            self.ffprobe, "-v", "error", "-show_streams", "-show_format",
            "-of", "json", path,
        ]
        log.debug("ffprobe futtatása: %s", os.path.basename(path))
        result = run_process(command, timeout=120)
        if result.returncode != 0:
            raise MediaError(f"Az ffprobe nem tudta beolvasni a fájlt:\n{result.stderr.strip()[:400]}")

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise MediaError(f"Az ffprobe válasza értelmezhetetlen: {exc}") from exc

        info = MediaInfo(path=path)
        try:
            info.duration = float(data.get("format", {}).get("duration", 0) or 0)
        except (TypeError, ValueError):
            info.duration = 0.0

        sub_counter = 0
        for stream in data.get("streams", []):
            if stream.get("codec_type") != "subtitle":
                continue
            tags = {k.lower(): v for k, v in (stream.get("tags") or {}).items()}
            disposition = stream.get("disposition") or {}
            track = SubtitleTrack(
                index=stream.get("index", 0),
                sub_index=sub_counter,
                codec=(stream.get("codec_name") or "ismeretlen").lower(),
                language=tags.get("language", ""),
                title=tags.get("title", ""),
                is_default=bool(disposition.get("default")),
                is_forced=bool(disposition.get("forced")),
            )
            for key in ("number_of_frames", "number_of_frames-eng"):
                if key in tags:
                    try:
                        track.event_count = int(tags[key])
                        break
                    except (TypeError, ValueError):
                        pass
            info.subtitle_tracks.append(track)
            # A meglévő flag-eket megjegyezzük, hogy muxoláskor ne vesszenek el.
            flags = [name for name in ("forced", "hearing_impaired", "visual_impaired", "comment")
                     if disposition.get(name)]
            info.dispositions.append("+".join(flags) if flags else "0")
            sub_counter += 1

        info.all_subtitle_count = sub_counter
        log.info(
            "%s: %d felirat sáv, hossz %.0f mp",
            os.path.basename(path), sub_counter, info.duration,
        )
        for track in info.subtitle_tracks:
            log.debug("  sáv %s", track.label)
        return info

    # ------------------------------------------------------------------
    def extract(self, path: str, track: SubtitleTrack, out_path: str) -> str:
        """Egy felirat sáv kimentése fájlba, lehetőleg eredeti formátumban."""
        if track.is_bitmap:
            raise MediaError(
                f"A(z) #{track.index} sáv képalapú felirat ({track.codec}), nincs benne szöveg.\n"
                "Válassz szöveges (subrip/ass) sávot, vagy tölts be külső SRT fájlt."
            )

        # ASS -> ASS másolás (tipográfia megmarad), minden más -> SRT.
        if track.codec in ("ass", "ssa"):
            codec_args = ["-c:s", "copy"]
        elif track.codec == "subrip":
            codec_args = ["-c:s", "copy"]
        else:
            codec_args = ["-c:s", "srt"]

        command = [
            self.ffmpeg, "-y", "-loglevel", "error",
            "-i", path, "-map", f"0:{track.index}", *codec_args, out_path,
        ]
        log.debug("Kinyerés: %s", " ".join(command[-6:]))
        result = run_process(command, timeout=600)

        if result.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            # Másolás nem ment - próbáljuk átkódolással.
            log.warning("Közvetlen másolás sikertelen, újrapróbálás átkódolással.")
            command = [
                self.ffmpeg, "-y", "-loglevel", "error",
                "-i", path, "-map", f"0:{track.index}", "-c:s", "srt",
                os.path.splitext(out_path)[0] + ".srt",
            ]
            result = run_process(command, timeout=600)
            out_path = os.path.splitext(out_path)[0] + ".srt"
            if result.returncode != 0 or not os.path.exists(out_path):
                raise MediaError(
                    f"A felirat kinyerése nem sikerült:\n{result.stderr.strip()[:400]}"
                )

        log.info("Felirat kinyerve: %s (%d bájt)", os.path.basename(out_path), os.path.getsize(out_path))
        return out_path

    # ------------------------------------------------------------------
    def mux_subtitle(
        self,
        mkv_path: str,
        subtitle_path: str,
        info: MediaInfo,
        output_path: str,
        set_default: bool = True,
        drop_existing_hungarian: bool = True,
        progress_cb=None,
    ) -> str:
        """A magyar feliratot beleteszi az MKV-be, alapértelmezett sávként.

        Videó és hang bitre pontosan másolódik - nincs újrakódolás, nincs
        minőségvesztés, és néhány másodperc alatt lefut.
        """
        # 1. Melyik meglévő felirat sávot dobjuk el? (korábbi magyar sávok)
        dropped: list[int] = []
        if drop_existing_hungarian:
            for track in info.subtitle_tracks:
                if track.is_hungarian:
                    dropped.append(track.index)
                    log.info("Korábbi magyar sáv eldobása: %s", track.label)

        command = [
            self.ffmpeg, "-y", "-loglevel", "error", "-nostats",
            "-i", mkv_path, "-i", subtitle_path,
        ]

        # 2. Sávtérkép. A magyar felirat szándékosan a LEGELSŐ felirat sáv lesz.
        #    Sok lejátszó (VLC, MPC-HC) nem a Matroska "default" jelzőt nézi, hanem
        #    a saját nyelvbeállítását, vagy egyszerűen az első felirat sávot veszi.
        #    Ha a magyar egyszerre első ÉS default, minden lejátszó eltalálja.
        command += ["-map", "0:v?"]          # videó (és borítókép, ha van)
        command += ["-map", "0:a?"]          # minden hangsáv
        command += ["-map", "1:0"]           # >>> a magyar felirat: 0. felirat sáv
        command += ["-map", "0:s?"]          # az eredeti felirat sávok utána
        for index in dropped:
            command += ["-map", f"-0:{index}"]
        command += ["-map", "0:t?"]          # csatolmányok: az ASS betűtípusok!
        command += ["-map", "0:d?"]          # adatfolyamok, ha vannak
        command += ["-map_chapters", "0"]    # fejezetek megtartása

        # 3. Másolás mindenhol, semmi újrakódolás.
        command += ["-c", "copy"]

        # 4. Az új sáv metaadatai: nyelv + magyar nyelvű megnevezés.
        command += [
            "-metadata:s:s:0", f"language={TARGET_LANG_ISO3}",
            "-metadata:s:s:0", f"title={TRACK_TITLE}",
        ]

        # 5. Alapértelmezett sáv: a magyar kapja, a többiről levesszük a jelölést.
        if set_default:
            command += ["-disposition:s:0", "default"]
            position = 1
            for track in info.subtitle_tracks:
                if track.index in dropped:
                    continue
                command += [f"-disposition:s:{position}", info.dispositions[track.sub_index]]
                position += 1

        command += ["-progress", "pipe:1", output_path]

        log.info("Muxolás indul -> %s", os.path.basename(output_path))
        log.debug("Muxolási parancs: %s", " ".join(command))
        self._run_with_progress(command, info.duration, progress_cb)

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise MediaError("A muxolás nem hozott létre érvényes kimeneti fájlt.")

        log.info("Muxolás kész: %s", os.path.basename(output_path))
        return output_path

    def _run_with_progress(self, command: list[str], duration: float, progress_cb) -> None:
        process = popen_process(command)
        tail: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if line.startswith("out_time_ms=") and duration > 0 and progress_cb:
                try:
                    done = int(line.split("=", 1)[1]) / 1_000_000.0
                    progress_cb(min(done / duration, 1.0))
                except (ValueError, ZeroDivisionError):
                    pass
            elif line and not line.startswith(("frame=", "fps=", "bitrate=", "total_size=",
                                               "out_time", "dup_frames", "drop_frames",
                                               "speed=", "progress=", "stream_")):
                tail.append(line)
                if len(tail) > 20:
                    tail.pop(0)
        process.wait()
        if process.returncode != 0:
            raise MediaError("Az ffmpeg hibával állt le:\n" + "\n".join(tail[-8:]))

    # ------------------------------------------------------------------
    def verify_muxed(self, path: str, expected_duration: float) -> bool:
        """Ellenőrzés muxolás után: tényleg bent van a magyar sáv, ép a fájl?"""
        try:
            info = self.probe(path)
        except MediaError as exc:
            log.error("Az elkészült fájl nem olvasható: %s", exc)
            return False

        hungarian = [t for t in info.subtitle_tracks if t.is_hungarian]
        if not hungarian:
            log.error("Ellenőrzés bukott: nincs magyar felirat sáv a kimenetben.")
            return False
        if expected_duration > 0 and abs(info.duration - expected_duration) > 2.0:
            log.error(
                "Ellenőrzés bukott: a hossz eltér (eredeti %.1f mp, új %.1f mp).",
                expected_duration, info.duration,
            )
            return False

        track = hungarian[-1]
        log.info(
            "Ellenőrzés rendben: '%s' (%s), alapértelmezett: %s",
            track.title, track.language, "igen" if track.is_default else "nem",
        )
        return True


# =========================================================================
#  Sávválasztás
# =========================================================================

def score_track(track: SubtitleTrack) -> int:
    """Pontozás: melyik sáv a legjobb fordítási forrás? (nagyobb = jobb)"""
    score = 0
    if track.is_bitmap:
        return -1000  # képalapú: nincs mit fordítani
    if track.is_hungarian:
        return -500   # már magyar

    language = track.language.lower()
    if language in LANG_PREFERENCE:
        # Az angol forrás megbízhatóbb fordítást ad, mint a japán.
        score += 100 - LANG_PREFERENCE.index(language) * 20
    elif language:
        score += 20

    title = track.title.lower()
    if any(hint in title for hint in PARTIAL_TRACK_HINTS):
        score -= 80   # csak feliratok/dalszövegek, nem teljes párbeszéd
    if track.is_forced:
        score -= 60   # kényszerített sáv: általában csak pár sor
    if "full" in title or "dialogue" in title or "dialog" in title:
        score += 30
    if track.is_default:
        score += 10
    if track.codec in ("ass", "ssa"):
        score += 5    # animénél jellemzően ez a rendes sáv
    if track.event_count:
        score += min(track.event_count // 50, 20)  # több sor = teljesebb sáv
    return score


def find_matching_track(tracks: list[SubtitleTrack],
                        preference: SubtitleTrack) -> SubtitleTrack | None:
    """Ugyanannak a sávnak a megkeresése egy másik fájlban.

    Kötegelt módban a sáv INDEXE fájlonként eltérhet, a nyelve és a címe
    viszont egy évadon belül általában ugyanaz. Ezért cím és nyelv alapján
    keresünk, egyre lazább feltételekkel.
    """
    usable = [t for t in tracks if not t.is_bitmap]
    if not usable:
        return None

    language = preference.language.lower()
    title = preference.title.lower().strip()

    # 1. nyelv + cím + kodek - ez a biztos találat
    for track in usable:
        if (track.language.lower() == language
                and track.title.lower().strip() == title
                and track.codec == preference.codec):
            return track
    # 2. nyelv + cím
    for track in usable:
        if track.language.lower() == language and track.title.lower().strip() == title:
            return track
    # 3. nyelv + kodek
    for track in usable:
        if track.language.lower() == language and track.codec == preference.codec:
            return track
    # 4. csak nyelv, a legteljesebb ilyen sávval
    same_language = [t for t in usable if t.language.lower() == language]
    if same_language:
        return max(same_language, key=score_track)
    return None


def pick_best_track(tracks: list[SubtitleTrack]) -> SubtitleTrack | None:
    """Automatikus sávválasztás fordításhoz."""
    candidates = [t for t in tracks if not t.is_bitmap and not t.is_hungarian]
    if not candidates:
        return None
    best = max(candidates, key=score_track)
    log.info("Automatikusan választott sáv: %s (pontszám %d)", best.label, score_track(best))
    return best


def wait_for_file(path: str, timeout: float = 5.0) -> bool:
    """Megvárja, hogy a fájl megjelenjen (lassú hálózati meghajtókhoz)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return True
        time.sleep(0.1)
    return False
