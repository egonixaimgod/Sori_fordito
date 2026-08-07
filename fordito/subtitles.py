"""Feliratkezelés: robusztus SRT/ASS beolvasás, formázásvédelem, profi sortördelés.

Itt dől el a feliratminőség nagy része. A fordítómotorok soha nem látnak
formázókódot: minden ASS/HTML jelölést leválasztunk, a modell tiszta mondatot
kap, és a fordítás után visszatesszük a jelöléseket a helyükre.
"""

from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass, field

from .logsetup import get_logger

log = get_logger("felirat")

# --- Mintázatok -----------------------------------------------------------
_ASS_OVERRIDE = re.compile(r"\{\\[^}]*\}")           # {\an8}, {\i1}, {\pos(..)}
_ASS_COMMENT = re.compile(r"\{(?!\\)[^}]*\}")        # {megjegyzés}
_HTML_TAG = re.compile(r"</?(?:i|b|u|s|font)(?:\s[^>]*)?>", re.IGNORECASE)
_ASS_DRAWING = re.compile(r"\{\\p[1-9]\d*\}")        # rajzparancs - ezt nem fordítjuk
_MUSIC = "♪♫∮"
# Mondatvégi írásjelek - a nyugati és a kelet-ázsiai változatok is.
_TERMINAL = ".!?…。！？」』〜"
_DASH_START = re.compile(r"^\s*[-–—]\s*")
_SRT_TIME_LINE = re.compile(r"\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}")
# Csak számokból, írásjelekből vagy szimbólumokból álló sor: nincs mit fordítani.
_NO_LETTERS = re.compile(r"^[\W\d_]*$", re.UNICODE)


# =========================================================================
#  Előkészített feliratsor: fordítható szövegdarabok + a köréjük tartozó dísz
# =========================================================================

@dataclass
class _Piece:
    """Egy fordítandó szövegdarab és a hozzá tartozó formázás."""
    text: str                       # tiszta, fordítandó szöveg
    prefix_tags: str = ""           # sor elejére visszakerülő ASS kódok
    suffix_tags: str = ""           # sor végére visszakerülő ASS kódok (pl. {\i0})
    dash: str = ""                  # párbeszédjel ("- ")
    italic: bool = False            # az egész darab dőlt volt
    music: bool = False             # zenei jel keretezte (dalszöveg)
    translatable: bool = True       # False: változatlanul megy vissza
    capitalize: bool = True         # mondatkezdő volt-e az eredeti darab


@dataclass
class PreparedLine:
    """Egy feliratblokk fordításra előkészítve."""
    pieces: list[_Piece] = field(default_factory=list)
    raw: str = ""

    @property
    def texts(self) -> list[str]:
        """A ténylegesen fordítandó szövegdarabok."""
        return [p.text for p in self.pieces if p.translatable]

    def render(self, translations: list[str], max_line_length: int = 42, max_lines: int = 2) -> str:
        """A lefordított darabokból újraépíti a formázott feliratblokkot."""
        out_lines: list[str] = []
        cursor = 0
        for piece in self.pieces:
            if piece.translatable:
                text = translations[cursor] if cursor < len(translations) else piece.text
                cursor += 1
            else:
                text = piece.text
            text = (text or "").strip()
            if not text:
                continue
            # A gépi fordítás gyakran kisbetűvel kezd. Ha az eredeti mondatkezdő
            # volt (vagy japán, ahol nincs kisbetű), nagybetűvel indítunk.
            if piece.capitalize and text[:1].islower():
                text = text[0].upper() + text[1:]
            if piece.music:
                text = f"♪ {text} ♪"
            # A párbeszédjeles daraboknál marad az egysoros forma, különben tördelünk.
            wrapped = wrap_text(text, max_line_length, max_lines)
            wrapped_lines = wrapped.split("\n")
            for i, wline in enumerate(wrapped_lines):
                decorated = wline
                if i == 0:
                    decorated = f"{piece.dash}{decorated}"
                if i == 0 and piece.prefix_tags:
                    decorated = f"{piece.prefix_tags}{decorated}"
                if i == len(wrapped_lines) - 1 and piece.suffix_tags:
                    decorated = f"{decorated}{piece.suffix_tags}"
                out_lines.append(decorated)
            if piece.italic and out_lines:
                # A teljes darabot fogja körbe a dőlt jelölés.
                start = len(out_lines) - len(wrapped_lines)
                out_lines[start] = "<i>" + out_lines[start]
                out_lines[-1] = out_lines[-1] + "</i>"
        return "\n".join(out_lines) if out_lines else self.raw


def is_translatable(text: str) -> bool:
    """Van-e egyáltalán fordítandó betű a szövegben?"""
    stripped = text.strip()
    if not stripped:
        return False
    return not _NO_LETTERS.match(stripped)


def prepare_line(raw: str) -> PreparedLine:
    """Feliratblokk szétszedése fordítható darabokra + formázásra."""
    prepared = PreparedLine(raw=raw)
    if not raw or not raw.strip():
        return prepared

    # Rajzparancsot tartalmazó sor (ASS grafika): érintetlenül hagyjuk.
    if _ASS_DRAWING.search(raw):
        prepared.pieces.append(_Piece(text=raw, translatable=False))
        return prepared

    text = raw.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    lines = [ln for ln in text.split("\n")]

    # Mikor bontjuk szét a sorokat, és mikor fűzzük össze?
    #  - Két gondolatjeles sor: párbeszéd, külön mondatok.
    #  - Minden sor mondatvégi írásjellel zárul: önálló megnyilatkozások.
    #  - Egyébként egyetlen mondat tördelése: össze kell fűzni, különben
    #    a fordító félmondatokat kapna és értelmetlen eredményt adna.
    nonempty = [ln for ln in lines if ln.strip()]
    dashed = [ln for ln in nonempty if _DASH_START.match(ln)]
    separate_utterances = (
        len(nonempty) >= 2
        and all(_strip_tags(ln).rstrip()[-1:] in _TERMINAL for ln in nonempty[:-1])
    )
    if len(dashed) >= 2 or separate_utterances:
        chunks = nonempty
    else:
        joined = " ".join(ln.strip() for ln in nonempty)
        chunks = [joined] if joined else []

    for chunk in chunks:
        prepared.pieces.append(_extract_piece(chunk))
    return prepared


def _strip_tags(text: str) -> str:
    """Formázókódok nélküli szöveg - a szerkezeti döntésekhez."""
    return _HTML_TAG.sub("", _ASS_COMMENT.sub("", _ASS_OVERRIDE.sub("", text))).strip()


def _extract_piece(chunk: str) -> _Piece:
    piece = _Piece(text="")

    # 1. Sor eleji ASS kódok (pozicionálás, stílus) megőrzése.
    prefix = ""
    while True:
        match = _ASS_OVERRIDE.match(chunk)
        if not match:
            break
        prefix += match.group(0)
        chunk = chunk[match.end():]
    piece.prefix_tags = prefix

    # 2. Sor végi ASS kódok (pl. a dőlt betűt lezáró {\i0}) megőrzése.
    suffix = ""
    while True:
        match = _ASS_OVERRIDE.search(chunk)
        if not match or match.end() != len(chunk.rstrip()):
            break
        suffix = match.group(0) + suffix
        chunk = chunk[:match.start()]
    piece.suffix_tags = suffix

    # 3. Belső ASS kódok és megjegyzések eldobása.
    chunk = _ASS_OVERRIDE.sub("", chunk)
    chunk = _ASS_COMMENT.sub("", chunk)

    # 4. Dőlt/félkövér jelölés: megjegyezzük, hogy körbeölelte-e a darabot.
    lowered = chunk.strip().lower()
    if lowered.startswith("<i>") and lowered.endswith("</i>"):
        piece.italic = True
    chunk = _HTML_TAG.sub("", chunk)

    # 5. Párbeszédjel leválasztása.
    dash_match = _DASH_START.match(chunk)
    if dash_match:
        piece.dash = "- "
        chunk = chunk[dash_match.end():]

    # 6. Zenei jelek (dalszöveg) leválasztása.
    stripped = chunk.strip()
    if stripped and (stripped[0] in _MUSIC or stripped[-1] in _MUSIC):
        piece.music = True
        chunk = stripped.strip(_MUSIC + " ")

    piece.text = chunk.strip()
    piece.translatable = is_translatable(piece.text)
    # Kisbetűvel kezdődő darab: valószínűleg az előző feliratból átfolyó mondat,
    # azt nem írjuk át nagybetűre. A japán/kínai írásjelek nem kisbetűsek, ezért
    # ott mindig mondatkezdésnek vesszük.
    piece.capitalize = not piece.text[:1].islower()
    return piece


# =========================================================================
#  Sortördelés - a nézhető felirat kulcsa
# =========================================================================

def wrap_text(text: str, max_length: int = 42, max_lines: int = 2) -> str:
    """Feliratsor tördelése kiegyensúlyozott sorokra.

    Szabvány felirat: legfeljebb 2 sor, soronként ~42 karakter, a törés
    lehetőleg írásjel után, kb. a szöveg felénél essen.
    """
    text = " ".join(text.split())
    if len(text) <= max_length:
        return text

    if max_lines >= 2 and len(text) <= max_length * 2:
        split_at = _best_break(text)
        if split_at:
            return text[:split_at].rstrip() + "\n" + text[split_at:].lstrip()

    # Hosszabb szöveg: mohó tördelés. Inkább legyen 3 sor, mint csonkolt felirat.
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > max_length:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return "\n".join(lines)


def _best_break(text: str) -> int | None:
    """A legjobb törési pont megkeresése két sorra bontáshoz."""
    middle = len(text) / 2
    best_pos, best_score = None, float("inf")
    for match in re.finditer(r" ", text):
        pos = match.start()
        if pos == 0 or pos >= len(text) - 1:
            continue
        score = abs(pos - middle)
        before = text[pos - 1]
        after = text[pos + 1:pos + 2]
        if before in ".!?…":
            score -= 14        # mondathatár: ideális törés
        elif before in ",;:":
            score -= 8         # tagmondathatár: jó törés
        if after and after in "-–—":
            score -= 6         # párbeszédjel elé jó törni
        if before in "([\"'":
            score += 12        # nyitó jel után ne törjünk
        if score < best_score:
            best_pos, best_score = pos, score
    return best_pos


# =========================================================================
#  SRT dokumentum
# =========================================================================

@dataclass
class Cue:
    index: int
    timing: str
    text: str


class SrtDocument:
    """Saját SRT olvasó/író - elbírja a sérült indexelést és a vegyes kódolást."""

    extension = ".srt"

    def __init__(self, cues: list[Cue], encoding: str = "utf-8"):
        self.cues = cues
        self.source_encoding = encoding

    # -- betöltés ---------------------------------------------------------
    @classmethod
    def load(cls, path: str) -> "SrtDocument":
        raw, encoding = _read_text(path)
        cues: list[Cue] = []
        blocks = re.split(r"\r?\n\s*\r?\n", raw.strip())
        for block in blocks:
            lines = [ln for ln in block.replace("\r\n", "\n").split("\n")]
            if not lines:
                continue
            timing_idx = next((i for i, ln in enumerate(lines) if _SRT_TIME_LINE.search(ln)), None)
            if timing_idx is None:
                log.debug("Időbélyeg nélküli blokk kihagyva: %.60s", block.replace("\n", " "))
                continue
            timing = lines[timing_idx].strip()
            text = "\n".join(lines[timing_idx + 1:]).strip()
            cues.append(Cue(index=len(cues) + 1, timing=timing, text=text))
        log.info("SRT beolvasva: %s (%d felirat, kódolás: %s)", os.path.basename(path), len(cues), encoding)
        return cls(cues, encoding)

    # -- szövegek ---------------------------------------------------------
    def get_texts(self) -> list[str]:
        return [cue.text for cue in self.cues]

    def set_texts(self, texts: list[str]) -> None:
        for cue, text in zip(self.cues, texts):
            cue.text = text

    def save(self, path: str) -> None:
        buffer = io.StringIO()
        for i, cue in enumerate(self.cues, start=1):
            buffer.write(f"{i}\n{cue.timing}\n{cue.text}\n\n")
        with open(path, "w", encoding="utf-8", newline="\r\n") as handle:
            handle.write(buffer.getvalue())
        log.info("SRT mentve: %s (%d felirat)", path, len(self.cues))


# =========================================================================
#  ASS/SSA dokumentum - a tipográfia megőrzésével
# =========================================================================

class AssDocument:
    """ASS felirat: csak a Dialogue sorok szövegmezőjét cseréljük, a stílusokat nem."""

    extension = ".ass"

    def __init__(self, lines: list[str], dialogue_positions: list[tuple[int, int]], encoding: str = "utf-8"):
        self.lines = lines
        # (sorindex, a szövegmező kezdőpozíciója a sorban)
        self.dialogue_positions = dialogue_positions
        self.source_encoding = encoding

    @classmethod
    def load(cls, path: str) -> "AssDocument":
        raw, encoding = _read_text(path)
        lines = raw.replace("\r\n", "\n").split("\n")
        text_field_index = 9  # ASS alapértelmezés: a Text a 10. mező
        positions: list[tuple[int, int]] = []
        for i, line in enumerate(lines):
            if line.lower().startswith("format:") and any(
                lines[j].strip().lower() == "[events]" for j in range(max(0, i - 3), i)
            ):
                fields_ = [f.strip().lower() for f in line.split(":", 1)[1].split(",")]
                if "text" in fields_:
                    text_field_index = fields_.index("text")
            if line.startswith("Dialogue:"):
                offset = _nth_comma_offset(line, text_field_index)
                if offset is not None:
                    positions.append((i, offset))
        log.info("ASS beolvasva: %s (%d dialógussor, kódolás: %s)", os.path.basename(path), len(positions), encoding)
        return cls(lines, positions, encoding)

    def get_texts(self) -> list[str]:
        return [self.lines[i][offset:] for i, offset in self.dialogue_positions]

    def set_texts(self, texts: list[str]) -> None:
        for (line_idx, offset), text in zip(self.dialogue_positions, texts):
            head = self.lines[line_idx][:offset]
            self.lines[line_idx] = head + text.replace("\n", "\\N")

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8", newline="\r\n") as handle:
            handle.write("\n".join(self.lines))
        log.info("ASS mentve: %s (%d dialógussor)", path, len(self.dialogue_positions))


def _nth_comma_offset(line: str, n: int) -> int | None:
    """A Dialogue sor n. vesszője utáni pozíció (a szövegmező kezdete)."""
    pos = line.find(":") + 1
    for _ in range(n):
        pos = line.find(",", pos)
        if pos == -1:
            return None
        pos += 1
    return pos


# =========================================================================
#  Kódolásfelismerés
# =========================================================================

def _read_text(path: str) -> tuple[str, str]:
    """Feliratfájl beolvasása: BOM és a gyakori kelet-európai kódolások kezelése."""
    with open(path, "rb") as handle:
        data = handle.read()
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16"), "utf-16"
    for encoding in ("utf-8-sig", "utf-8", "cp1250", "iso-8859-2", "cp1252"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    log.warning("Ismeretlen kódolás (%s), latin-1 kényszerítése.", os.path.basename(path))
    return data.decode("latin-1", errors="replace"), "latin-1"


def load_document(path: str):
    """A kiterjesztés alapján a megfelelő feliratdokumentumot adja vissza."""
    if path.lower().endswith((".ass", ".ssa")):
        return AssDocument.load(path)
    return SrtDocument.load(path)
