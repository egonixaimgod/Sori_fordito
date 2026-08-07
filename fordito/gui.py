"""Grafikus felület - áttekinthető, egyszerű, és mindig látszik rajta, mi történik."""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from . import logsetup
from .config import Settings, TRACK_TITLE
from .logsetup import get_logger
from .media import FFmpeg, MediaError, pick_best_track
from .pipeline import Callbacks, JobResult, JobStatus, Pipeline
from .translator import Translator
from .util import human_time, shorten_path

log = get_logger("felulet")

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# --- Színek: visszafogott, egységes paletta ------------------------------
COL_BG = "#16181d"
COL_CARD = "#1e2128"
COL_CARD_2 = "#262a33"
COL_ACCENT = "#3d7eff"
COL_ACCENT_HOVER = "#2f66d8"
COL_OK = "#3fb950"
COL_WARN = "#d29922"
COL_ERR = "#f05d5d"
COL_MUTED = "#8b93a1"
COL_TEXT = "#e6e9ef"

FONT_TITLE = ("Segoe UI Semibold", 19)
FONT_CARD = ("Segoe UI Semibold", 12)
FONT_BODY = ("Segoe UI", 12)
FONT_SMALL = ("Segoe UI", 11)
FONT_MONO = ("Consolas", 11)

LOG_LEVELS = {"Minden (részletes)": logging.DEBUG, "Normál": logging.INFO,
              "Csak figyelmeztetés": logging.WARNING, "Csak hiba": logging.ERROR}
ENGINE_CHOICES = {
    "Automatikus (ajánlott)": "auto",
    "Microsoft fordító": "bing",
    "Google Translate": "google",
}
STATUS_COLORS = {
    JobStatus.OK: COL_OK,
    JobStatus.SKIPPED: COL_MUTED,
    JobStatus.ERROR: COL_ERR,
    JobStatus.CANCELLED: COL_WARN,
}


def _card(parent, title: str) -> ctk.CTkFrame:
    """Egységes kártya keret címmel."""
    frame = ctk.CTkFrame(parent, fg_color=COL_CARD, corner_radius=10)
    label = ctk.CTkLabel(frame, text=title, font=FONT_CARD, text_color=COL_MUTED, anchor="w")
    label.grid(row=0, column=0, columnspan=6, padx=14, pady=(10, 4), sticky="w")
    frame.grid_columnconfigure(1, weight=1)
    return frame


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()

        self.settings = Settings.load()
        self.ffmpeg = FFmpeg()
        self.translator = Translator(self.settings)
        self.pipeline = Pipeline(self.settings, self.ffmpeg, self.translator)

        self.mode = "file"                       # file | folder
        self.source_path = ""
        self.media_info = None
        self.tracks = []
        self.queue_paths: list[str] = []
        self.row_widgets: dict[str, tuple] = {}
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.started_at = 0.0
        self.completed_files = 0

        self.title("Magyar Felirat Fordító")
        self.geometry("1180x820")
        self.minsize(980, 700)
        self.configure(fg_color=COL_BG)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()
        self._build_sidebar()
        self._build_main()

        logsetup.set_gui_level(LOG_LEVELS.get(self.settings.gui_log_level, logging.INFO))
        self.after(120, self._drain_log)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._check_environment()

    # ==================================================================
    #  Felépítés
    # ==================================================================
    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=COL_CARD, corner_radius=0, height=64)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        header.grid_propagate(False)

        ctk.CTkLabel(header, text="  Magyar Felirat Fordító", font=FONT_TITLE,
                     text_color=COL_TEXT).grid(row=0, column=0, padx=(18, 0), pady=(12, 0), sticky="w")
        ctk.CTkLabel(header, text="   MKV feliratok fordítása magyarra – helyben, fiók és API kulcs nélkül",
                     font=FONT_SMALL, text_color=COL_MUTED).grid(row=1, column=0, padx=(20, 0),
                                                                 pady=(0, 10), sticky="w")

        self.engine_badge = ctk.CTkLabel(
            header, text="  motor keresése…  ", font=FONT_SMALL, text_color=COL_MUTED,
            fg_color=COL_CARD_2, corner_radius=12, height=26,
        )
        self.engine_badge.grid(row=0, column=2, rowspan=2, padx=18)

    # ------------------------------------------------------------------
    def _build_sidebar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=COL_CARD, corner_radius=0, width=250)
        bar.grid(row=1, column=0, sticky="nsw")
        bar.grid_propagate(False)
        bar.grid_rowconfigure(8, weight=1)

        def section(text: str, row: int, pady=(16, 4)) -> None:
            ctk.CTkLabel(bar, text=text, font=FONT_CARD, text_color=COL_MUTED, anchor="w").grid(
                row=row, column=0, padx=18, pady=pady, sticky="ew")

        section("MIT FORDÍTUNK?", 0, (18, 6))
        self.mode_switch = ctk.CTkSegmentedButton(
            bar, values=["Egy fájl", "Egész mappa"], command=self._on_mode_change,
            font=FONT_BODY, selected_color=COL_ACCENT, selected_hover_color=COL_ACCENT_HOVER,
        )
        self.mode_switch.set("Egy fájl")
        self.mode_switch.grid(row=1, column=0, padx=18, pady=(0, 6), sticky="ew")

        section("FORDÍTÓMOTOR", 2)
        self.engine_menu = ctk.CTkOptionMenu(
            bar, values=list(ENGINE_CHOICES.keys()), command=self._on_engine_change,
            font=FONT_BODY, fg_color=COL_CARD_2, button_color=COL_CARD_2,
            button_hover_color=COL_ACCENT, dynamic_resizing=False,
        )
        for label, value in ENGINE_CHOICES.items():
            if value == self.settings.engine:
                self.engine_menu.set(label)
        self.engine_menu.grid(row=3, column=0, padx=18, pady=(0, 6), sticky="ew")

        self.engine_status = ctk.CTkLabel(
            bar, text="Ellenőrzés…", font=FONT_SMALL, text_color=COL_MUTED,
            wraplength=210, justify="left", anchor="w",
        )
        self.engine_status.grid(row=4, column=0, padx=18, pady=(0, 6), sticky="ew")

        section("VEZÉRLÉS", 5)
        self.start_btn = ctk.CTkButton(
            bar, text="▶   FORDÍTÁS INDÍTÁSA", font=("Segoe UI Semibold", 13), height=44,
            fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER, command=self._start,
        )
        self.start_btn.grid(row=6, column=0, padx=18, pady=(0, 8), sticky="ew")

        self.stop_btn = ctk.CTkButton(
            bar, text="■   Megállítás", font=FONT_BODY, height=34, state="disabled",
            fg_color=COL_CARD_2, hover_color=COL_ERR, command=self._stop,
        )
        self.stop_btn.grid(row=7, column=0, padx=18, pady=(0, 8), sticky="ew")

        footer = ctk.CTkFrame(bar, fg_color="transparent")
        footer.grid(row=9, column=0, padx=18, pady=(0, 16), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(footer, text="⚙  Beállítások", font=FONT_BODY, height=32,
                      fg_color=COL_CARD_2, hover_color=COL_ACCENT,
                      command=self._open_settings).grid(row=0, column=0, pady=3, sticky="ew")
        ctk.CTkButton(footer, text="📄  Debug napló megnyitása", font=FONT_BODY, height=32,
                      fg_color=COL_CARD_2, hover_color=COL_ACCENT,
                      command=self._open_log).grid(row=1, column=0, pady=3, sticky="ew")

    # ------------------------------------------------------------------
    def _build_main(self) -> None:
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=1, column=1, sticky="nsew", padx=16, pady=16)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=3)
        main.grid_rowconfigure(3, weight=2)

        # --- 1. Forrás ------------------------------------------------
        source = _card(main, "1 ·  FORRÁS")
        source.grid(row=0, column=0, sticky="ew", pady=(0, 12))

        self.path_entry = ctk.CTkEntry(source, placeholder_text="Válassz MKV fájlt vagy mappát…",
                                       font=FONT_BODY, height=36, fg_color=COL_CARD_2, border_width=0)
        self.path_entry.grid(row=1, column=0, columnspan=2, padx=(14, 8), pady=(0, 12), sticky="ew")

        self.browse_btn = ctk.CTkButton(source, text="Tallózás…", width=110, height=36, font=FONT_BODY,
                                        fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER,
                                        command=self._browse)
        self.browse_btn.grid(row=1, column=2, padx=(0, 14), pady=(0, 12))

        self.track_label = ctk.CTkLabel(source, text="Forrás felirat sáv:", font=FONT_BODY,
                                        text_color=COL_MUTED)
        self.track_menu = ctk.CTkOptionMenu(source, values=["Automatikus – a legjobb sáv"],
                                            font=FONT_BODY, height=32, fg_color=COL_CARD_2,
                                            button_color=COL_CARD_2, button_hover_color=COL_ACCENT,
                                            dynamic_resizing=False)
        self.track_label.grid(row=2, column=0, padx=(14, 8), pady=(0, 14), sticky="w")
        self.track_menu.grid(row=2, column=1, columnspan=2, padx=(0, 14), pady=(0, 14), sticky="ew")

        # --- 2. Munkalista --------------------------------------------
        queue_card = _card(main, "2 ·  MUNKALISTA")
        queue_card.grid(row=1, column=0, sticky="nsew", pady=(0, 12))
        queue_card.grid_rowconfigure(1, weight=1)

        self.queue_frame = ctk.CTkScrollableFrame(queue_card, fg_color=COL_CARD_2, corner_radius=8)
        self.queue_frame.grid(row=1, column=0, columnspan=6, padx=14, pady=(0, 14), sticky="nsew")
        self.queue_frame.grid_columnconfigure(0, weight=1)
        self.queue_empty = ctk.CTkLabel(self.queue_frame, text="Még nincs kiválasztva semmi.",
                                        font=FONT_BODY, text_color=COL_MUTED)
        self.queue_empty.grid(row=0, column=0, padx=12, pady=12, sticky="w")

        # --- 3. Haladás -----------------------------------------------
        progress = _card(main, "3 ·  HALADÁS")
        progress.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        progress.grid_columnconfigure(0, weight=1)

        self.stage_label = ctk.CTkLabel(progress, text="Készen állok.", font=FONT_BODY,
                                        text_color=COL_TEXT, anchor="w")
        self.stage_label.grid(row=1, column=0, columnspan=2, padx=14, sticky="ew")

        self.file_bar = ctk.CTkProgressBar(progress, height=10, corner_radius=5,
                                           progress_color=COL_ACCENT, fg_color=COL_CARD_2)
        self.file_bar.set(0)
        self.file_bar.grid(row=2, column=0, columnspan=2, padx=14, pady=(6, 10), sticky="ew")

        self.overall_label = ctk.CTkLabel(progress, text="Összesen: –", font=FONT_SMALL,
                                          text_color=COL_MUTED, anchor="w")
        self.overall_label.grid(row=3, column=0, padx=14, sticky="w")
        self.eta_label = ctk.CTkLabel(progress, text="", font=FONT_SMALL, text_color=COL_MUTED,
                                      anchor="e")
        self.eta_label.grid(row=3, column=1, padx=14, sticky="e")

        self.overall_bar = ctk.CTkProgressBar(progress, height=6, corner_radius=3,
                                              progress_color=COL_OK, fg_color=COL_CARD_2)
        self.overall_bar.set(0)
        self.overall_bar.grid(row=4, column=0, columnspan=2, padx=14, pady=(6, 14), sticky="ew")

        # --- 4. Napló --------------------------------------------------
        log_card = _card(main, "4 ·  ESEMÉNYNAPLÓ")
        log_card.grid(row=3, column=0, sticky="nsew")
        log_card.grid_rowconfigure(1, weight=1)
        log_card.grid_columnconfigure(0, weight=1)

        self.level_menu = ctk.CTkOptionMenu(log_card, values=list(LOG_LEVELS.keys()), width=170,
                                            height=26, font=FONT_SMALL, fg_color=COL_CARD_2,
                                            button_color=COL_CARD_2, button_hover_color=COL_ACCENT,
                                            command=self._on_level_change)
        self.level_menu.set(next((k for k, v in LOG_LEVELS.items()
                                  if v == LOG_LEVELS.get(self.settings.gui_log_level, logging.INFO)),
                                 "Normál"))
        self.level_menu.grid(row=0, column=4, padx=(0, 8), pady=(8, 4), sticky="e")
        ctk.CTkButton(log_card, text="Ürítés", width=70, height=26, font=FONT_SMALL,
                      fg_color=COL_CARD_2, hover_color=COL_ACCENT,
                      command=self._clear_log).grid(row=0, column=5, padx=(0, 14), pady=(8, 4), sticky="e")

        self.log_box = ctk.CTkTextbox(log_card, font=FONT_MONO, fg_color=COL_CARD_2,
                                      corner_radius=8, wrap="none", text_color=COL_TEXT)
        self.log_box.grid(row=1, column=0, columnspan=6, padx=14, pady=(0, 14), sticky="nsew")
        self.log_box.configure(state="disabled")
        for tag, color in (("WARNING", COL_WARN), ("ERROR", COL_ERR), ("CRITICAL", COL_ERR)):
            self.log_box.tag_config(tag, foreground=color)

    # ==================================================================
    #  Indulási ellenőrzés
    # ==================================================================
    def _check_environment(self) -> None:
        def check() -> None:
            if not self.ffmpeg.available():
                self._ui(messagebox.showerror, "Hiányzó ffmpeg",
                         "Az ffmpeg.exe nem található a program mellett és a PATH-ban sem.\n"
                         "Enélkül nem tudok MKV fájlt kezelni.")
            self._refresh_engine_status()

        threading.Thread(target=check, daemon=True, name="kornyezet").start()

    def _refresh_engine_status(self) -> None:
        """A választott motor állapotának lekérdezése háttérben."""
        def check() -> None:
            preference = self.settings.engine
            engine = self.translator.get_engine("google" if preference == "google" else "bing")
            status = engine.status()

            if status.available:
                text = f"{engine.display_name}\n{status.detail}"
                color = COL_OK
                badge = "  Microsoft  " if engine.name == "bing" else "  Google  "
            elif preference == "auto":
                google = self.translator.get_engine("google").status()
                if google.available:
                    text = f"Microsoft: {status.message}\n→ a Google Translate lép a helyére."
                    color, badge = COL_WARN, "  Google  "
                else:
                    text = "Egyik fordítószolgáltatás sem érhető el.\nEllenőrizd az internetkapcsolatot."
                    color, badge = COL_ERR, "  nincs net  "
            else:
                text = f"{status.message}\n{status.detail}"
                color, badge = COL_ERR, "  nem elérhető  "

            self._ui(lambda: (self.engine_status.configure(text=text, text_color=color),
                              self.engine_badge.configure(text=badge, text_color=color)))

        threading.Thread(target=check, daemon=True, name="motor-teszt").start()

    # ==================================================================
    #  Események
    # ==================================================================
    def _on_mode_change(self, value: str) -> None:
        self.mode = "folder" if value == "Egész mappa" else "file"
        log.info("Mód: %s", "egész mappa" if self.mode == "folder" else "egy fájl")
        self.path_entry.configure(placeholder_text=(
            "Válassz mappát, minden MKV lefordul benne…" if self.mode == "folder"
            else "Válassz MKV vagy felirat fájlt…"))
        # A sávválasztó mindkét módban látszik. Mappás módban a mappa első
        # fájljának sávjait kínáljuk, és a választott sávot minden fájlban
        # megkeressük - nyelv és cím alapján, mert az index fájlonként csúszhat.
        self.track_label.configure(
            text="Forrás sáv minden fájlhoz:" if self.mode == "folder" else "Forrás felirat sáv:")
        self._set_source("")

    def _on_engine_change(self, label: str) -> None:
        self.settings.engine = ENGINE_CHOICES[label]
        self.settings.save()
        log.info("Fordítómotor beállítva: %s", self.settings.engine)
        self.engine_status.configure(text="Ellenőrzés…", text_color=COL_MUTED)
        self._refresh_engine_status()

    def _on_level_change(self, label: str) -> None:
        logsetup.set_gui_level(LOG_LEVELS[label])
        self.settings.gui_log_level = label if label in LOG_LEVELS else "Normál"
        self.settings.save()

    def _browse(self) -> None:
        if self.mode == "folder":
            folder = filedialog.askdirectory(title="Válassz mappát",
                                             initialdir=self.settings.last_folder_dir or None)
            if folder:
                self.settings.last_folder_dir = folder
                self.settings.save()
                self._set_source(folder)
        else:
            path = filedialog.askopenfilename(
                title="Válassz fájlt",
                initialdir=self.settings.last_file_dir or None,
                filetypes=[("Videó és felirat", "*.mkv *.srt *.ass *.ssa"),
                           ("MKV videó", "*.mkv"), ("Felirat", "*.srt *.ass *.ssa"),
                           ("Minden fájl", "*.*")],
            )
            if path:
                self.settings.last_file_dir = os.path.dirname(path)
                self.settings.save()
                self._set_source(path)

    # ------------------------------------------------------------------
    def _set_source(self, path: str) -> None:
        self.source_path = path
        self.path_entry.delete(0, "end")
        if path:
            self.path_entry.insert(0, path)
        self._clear_queue()
        if not path:
            return

        log.info("Kiválasztva: %s", path)
        if self.mode == "folder":
            threading.Thread(target=self._scan_folder, daemon=True, name="mappa").start()
        else:
            self.queue_paths = [path]
            self._render_queue()
            if path.lower().endswith(".mkv"):
                threading.Thread(target=self._analyze, daemon=True, name="elemzes").start()
            else:
                self.track_menu.configure(values=["Külön feliratfájl – nincs sávválasztás"])
                self.track_menu.set("Külön feliratfájl – nincs sávválasztás")

    def _scan_folder(self) -> None:
        self._ui(self.stage_label.configure, text="Mappa átnézése…")
        try:
            paths = self.pipeline.scan_folder(self.source_path)
        except Exception as exc:
            log.exception("A mappa nem olvasható")
            self._ui(messagebox.showerror, "Hiba", f"A mappa nem olvasható:\n{exc}")
            return
        self.queue_paths = paths
        self._ui(self._render_queue)
        if not paths:
            self._ui(self.stage_label.configure, text="Nem találtam MKV fájlt ebben a mappában.")
            return
        self._ui(self.stage_label.configure, text=f"{len(paths)} MKV fájl a sorban.")

        # A sávlistát a mappa első fájljából olvassuk ki - egy évadon belül
        # a sávok elrendezése jellemzően minden epizódban azonos.
        self._ui(self.stage_label.configure,
                 text=f"{len(paths)} MKV fájl · sávok beolvasása az első fájlból…")
        try:
            info = self.ffmpeg.probe(paths[0])
        except MediaError as exc:
            log.warning("Az első fájl sávjai nem olvashatók: %s", exc)
            self._ui(self.stage_label.configure, text=f"{len(paths)} MKV fájl a sorban.")
            return

        self.media_info = info
        self.tracks = info.subtitle_tracks
        best = pick_best_track(self.tracks)
        values = ["Automatikus – fájlonként a legjobb sáv"] + [t.label for t in self.tracks]

        def apply() -> None:
            self.track_menu.configure(values=values)
            self.track_menu.set(values[0])
            extra = f" · ajánlott: {best.label}" if best else ""
            self.stage_label.configure(
                text=f"{len(paths)} MKV fájl · {len(self.tracks)} felirat sáv az első fájlban{extra}")

        self._ui(apply)

    def _analyze(self) -> None:
        self._ui(self.stage_label.configure, text="Felirat sávok elemzése…")
        try:
            info = self.ffmpeg.probe(self.source_path)
        except MediaError as exc:
            log.error("Elemzési hiba: %s", exc)
            self._ui(messagebox.showerror, "Nem sikerült beolvasni", str(exc))
            self._ui(self.stage_label.configure, text="Az elemzés nem sikerült.")
            return

        self.media_info = info
        self.tracks = info.subtitle_tracks
        best = pick_best_track(self.tracks)
        values = ["Automatikus – a legjobb sáv"] + [t.label for t in self.tracks]

        def apply() -> None:
            self.track_menu.configure(values=values)
            self.track_menu.set(values[0])
            if not self.tracks:
                self.stage_label.configure(text="Nincs felirat sáv ebben a fájlban.")
            else:
                extra = f"  ·  ajánlott: {best.label}" if best else "  ·  csak képalapú sáv van!"
                self.stage_label.configure(text=f"{len(self.tracks)} felirat sáv{extra}")
            self._render_queue()

        self._ui(apply)

    # ==================================================================
    #  Munkalista
    # ==================================================================
    def _clear_queue(self) -> None:
        for widgets in self.row_widgets.values():
            for widget in widgets:
                widget.destroy()
        self.row_widgets.clear()
        self.queue_paths = []
        self.queue_empty.grid()
        self.overall_bar.set(0)
        self.overall_label.configure(text="Összesen: –")
        self.eta_label.configure(text="")

    def _render_queue(self) -> None:
        for widgets in self.row_widgets.values():
            for widget in widgets:
                widget.destroy()
        self.row_widgets.clear()

        if not self.queue_paths:
            self.queue_empty.grid()
            return
        self.queue_empty.grid_remove()

        base = self.source_path if self.mode == "folder" else os.path.dirname(self.source_path)
        for row, path in enumerate(self.queue_paths):
            try:
                display = os.path.relpath(path, base)
            except ValueError:
                display = os.path.basename(path)
            name = ctk.CTkLabel(self.queue_frame, text=f"  {row + 1}.  {display}", font=FONT_BODY,
                                text_color=COL_TEXT, anchor="w")
            name.grid(row=row, column=0, padx=(6, 10), pady=2, sticky="ew")
            state = ctk.CTkLabel(self.queue_frame, text="várakozik", font=FONT_SMALL,
                                 text_color=COL_MUTED, anchor="e", width=210)
            state.grid(row=row, column=1, padx=(0, 10), pady=2, sticky="e")
            self.row_widgets[path] = (name, state)

        self.overall_label.configure(text=f"Összesen: 0 / {len(self.queue_paths)} fájl")

    def _set_row(self, path: str, text: str, color: str) -> None:
        widgets = self.row_widgets.get(path)
        if widgets:
            widgets[1].configure(text=text, text_color=color)

    # ==================================================================
    #  Futtatás
    # ==================================================================
    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.queue_paths:
            messagebox.showwarning("Nincs mit fordítani",
                                   "Előbb válassz ki egy fájlt vagy egy mappát!")
            return

        # A kiválasztott sáv mindkét módban él. Egy fájlnál pontosan ezt a sávot
        # használjuk, mappánál mintaként: minden fájlban ezt keressük meg.
        chosen_track = None
        if self.tracks:
            selected = self.track_menu.get()
            chosen_track = next((t for t in self.tracks if t.label == selected), None)
            if chosen_track and chosen_track.is_bitmap:
                messagebox.showerror(
                    "Képalapú felirat",
                    "Ez a sáv képekből áll (PGS/VobSub), nincs benne szöveg, amit lefordíthatnék.\n"
                    "Válassz szöveges sávot (subrip vagy ass).")
                return

        self.cancel_event.clear()
        self.started_at = time.monotonic()
        self.completed_files = 0
        self.start_btn.configure(state="disabled", text="⏳   Fordítás folyamatban…")
        self.stop_btn.configure(state="normal")
        self.browse_btn.configure(state="disabled")
        self.mode_switch.configure(state="disabled")
        for path in self.queue_paths:
            self._set_row(path, "várakozik", COL_MUTED)

        log.info("#" * 70)
        log.info("INDÍTÁS – %d fájl, mód: %s, motor: %s",
                 len(self.queue_paths), self.mode, self.settings.engine)
        log.info("Beillesztés MKV-be: %s | alapértelmezett sáv: %s | eredeti felülírása: %s",
                 self.settings.mux_into_mkv, self.settings.set_as_default, self.settings.replace_original)
        log.info("#" * 70)

        self.worker = threading.Thread(target=self._run, args=(chosen_track,),
                                       daemon=True, name="munka")
        self.worker.start()

    def _run(self, chosen_track) -> None:
        callbacks = Callbacks(
            on_stage=lambda text: self._ui(self.stage_label.configure, text=text),
            on_progress=lambda fraction: self._ui(self.file_bar.set, fraction),
            on_file_start=self._on_file_start,
            on_file_done=self._on_file_done,
        )
        try:
            if len(self.queue_paths) == 1:
                callbacks.on_file_start(1, 1, self.queue_paths[0])
                # Egyetlen, kézzel kiválasztott fájlnál sosem hagyunk ki semmit:
                # ha a felhasználó ezt a fájlt jelölte ki, azt akarja, hogy lefusson.
                result = self.pipeline.process_file(
                    self.queue_paths[0], track=chosen_track,
                    callbacks=callbacks, cancel=self.cancel_event, allow_skip=False)
                callbacks.on_file_done(result)
                results = [result]
            else:
                results = self.pipeline.process_many(
                    self.queue_paths, callbacks, self.cancel_event,
                    preference=chosen_track)
        except Exception as exc:
            log.exception("Váratlan hiba a feldolgozás közben")
            self._ui(messagebox.showerror, "Váratlan hiba", str(exc))
            results = []
        finally:
            self._ui(self._finish, results)

    def _on_file_start(self, index: int, total: int, path: str) -> None:
        def apply() -> None:
            self.file_bar.set(0)
            self._set_row(path, "feldolgozás alatt…", COL_ACCENT)
            self.overall_label.configure(text=f"Összesen: {index - 1} / {total} fájl kész")
            widgets = self.row_widgets.get(path)
            if widgets:
                widgets[0].configure(text_color=COL_ACCENT)
        self._ui(apply)

    def _on_file_done(self, result: JobResult) -> None:
        self.completed_files += 1

        def apply() -> None:
            color = STATUS_COLORS.get(result.status, COL_MUTED)
            if result.status is JobStatus.OK:
                text = f"kész · {human_time(result.seconds)}"
            elif result.status is JobStatus.SKIPPED:
                text = f"kihagyva · {result.message[:34]}"
            elif result.status is JobStatus.CANCELLED:
                text = "megszakítva"
            else:
                text = f"HIBA · {result.message[:34]}"
            self._set_row(result.path, text, color)
            widgets = self.row_widgets.get(result.path)
            if widgets:
                widgets[0].configure(text_color=COL_TEXT)

            total = len(self.queue_paths)
            self.overall_bar.set(self.completed_files / total if total else 0)
            self.overall_label.configure(text=f"Összesen: {self.completed_files} / {total} fájl kész")

            if self.completed_files < total:
                elapsed = time.monotonic() - self.started_at
                per_file = elapsed / max(self.completed_files, 1)
                remaining = per_file * (total - self.completed_files)
                self.eta_label.configure(text=f"hátralévő idő kb. {human_time(remaining)}")
            else:
                self.eta_label.configure(text="")

        self._ui(apply)

    def _finish(self, results: list[JobResult]) -> None:
        self.start_btn.configure(state="normal", text="▶   FORDÍTÁS INDÍTÁSA")
        self.stop_btn.configure(state="disabled")
        self.browse_btn.configure(state="normal")
        self.mode_switch.configure(state="normal")
        self.file_bar.set(0)

        ok = [r for r in results if r.status is JobStatus.OK]
        skipped = [r for r in results if r.status is JobStatus.SKIPPED]
        failed = [r for r in results if r.status is JobStatus.ERROR]
        elapsed = time.monotonic() - self.started_at

        if self.cancel_event.is_set():
            self.stage_label.configure(text="Megszakítva.")
            return

        self.stage_label.configure(
            text=f"Kész!  {len(ok)} sikeres, {len(skipped)} kihagyott, {len(failed)} hibás  ·  {human_time(elapsed)}")

        lines = [f"Elkészült {len(ok)} fájl {human_time(elapsed)} alatt."]
        if self.settings.mux_into_mkv and ok:
            lines.append(f"\nA magyar felirat „{TRACK_TITLE}” néven bekerült az MKV fájlokba, "
                         "alapértelmezett sávként.")
        if skipped:
            lines.append(f"\nKihagyva ({len(skipped)}):")
            lines += [f"  · {r.name}: {r.message}" for r in skipped[:5]]
        if failed:
            lines.append(f"\nHibás ({len(failed)}):")
            lines += [f"  · {r.name}: {r.message[:80]}" for r in failed[:5]]
            lines.append("\nA részletek a debug naplóban vannak.")

        if failed:
            messagebox.showwarning("Kész, de volt hiba", "\n".join(lines))
        else:
            messagebox.showinfo("Kész", "\n".join(lines))

    def _stop(self) -> None:
        log.warning("A felhasználó megszakította a feldolgozást.")
        self.cancel_event.set()
        self.stage_label.configure(text="Megszakítás… (a futó lépés még befejeződik)")
        self.stop_btn.configure(state="disabled")

    # ==================================================================
    #  Napló
    # ==================================================================
    def _drain_log(self) -> None:
        """A naplósorok átemelése a felületre - egyszerre legfeljebb 200, hogy ne akadjon."""
        lines: list[tuple[int, str]] = []
        try:
            for _ in range(200):
                lines.append(logsetup.gui_queue.get_nowait())
        except queue.Empty:
            pass

        if lines:
            self.log_box.configure(state="normal")
            at_bottom = self.log_box.yview()[1] > 0.98
            for level, text in lines:
                tag = logging.getLevelName(level) if level >= logging.WARNING else None
                self.log_box.insert("end", text + "\n", tag or ())
            # A doboz ne hízzon a végtelenségig - a teljes napló úgyis a fájlban van.
            if int(self.log_box.index("end-1c").split(".")[0]) > 2000:
                self.log_box.delete("1.0", "800.0")
            if at_bottom:
                self.log_box.see("end")
            self.log_box.configure(state="disabled")

        self.after(120, self._drain_log)

    def _clear_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _open_log(self) -> None:
        path = logsetup.log_path()
        if not os.path.exists(path):
            messagebox.showinfo("Napló", "A naplófájl még nem jött létre.")
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except AttributeError:
            subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            messagebox.showerror("Nem sikerült megnyitni", f"{path}\n\n{exc}")

    # ==================================================================
    #  Segédek
    # ==================================================================
    def _ui(self, func, *args, **kwargs) -> None:
        """Bármely szálból biztonságosan futtat felület-frissítést."""
        try:
            self.after(0, lambda: func(*args, **kwargs))
        except (RuntimeError, tk.TclError):
            pass  # az ablak épp bezárult

    def _open_settings(self) -> None:
        SettingsWindow(self)

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Kilépés", "Épp fut egy fordítás. Biztosan kilépsz?"):
                return
            self.cancel_event.set()
        self.settings.save()
        log.info("A program leáll.")
        self.destroy()


# =========================================================================
#  Beállítások ablak
# =========================================================================

class SettingsWindow(ctk.CTkToplevel):
    def __init__(self, master: App):
        super().__init__(master)
        self.app = master
        self.settings = master.settings

        self.title("Beállítások")
        self.geometry("620x680")
        self.configure(fg_color=COL_BG)
        self.transient(master)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        tabs = ctk.CTkTabview(self, fg_color=COL_CARD, segmented_button_selected_color=COL_ACCENT)
        tabs.grid(row=0, column=0, padx=16, pady=16, sticky="nsew")
        for name in ("MKV kimenet", "Fordítás", "Névszótár", "Haladó"):
            tabs.add(name)

        self._build_output(tabs.tab("MKV kimenet"))
        self._build_translation(tabs.tab("Fordítás"))
        self._build_glossary(tabs.tab("Névszótár"))
        self._build_advanced(tabs.tab("Haladó"))

        ctk.CTkButton(self, text="Mentés és bezárás", height=38, font=FONT_CARD,
                      fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER,
                      command=self._save).grid(row=1, column=0, padx=16, pady=(0, 16), sticky="ew")
        self.after(100, self.grab_set)

    # ------------------------------------------------------------------
    def _switch(self, parent, text: str, attribute: str, hint: str = "") -> None:
        variable = tk.BooleanVar(value=bool(getattr(self.settings, attribute)))
        setattr(self, f"var_{attribute}", variable)
        switch = ctk.CTkSwitch(parent, text=text, variable=variable, font=FONT_BODY,
                               progress_color=COL_ACCENT)
        switch.pack(anchor="w", padx=18, pady=(12, 0))
        if hint:
            ctk.CTkLabel(parent, text=hint, font=FONT_SMALL, text_color=COL_MUTED,
                         wraplength=520, justify="left").pack(anchor="w", padx=(46, 18), pady=(2, 0))

    def _number(self, parent, text: str, attribute: str, hint: str = "") -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(14, 0))
        ctk.CTkLabel(row, text=text, font=FONT_BODY, anchor="w").pack(side="left")
        entry = ctk.CTkEntry(row, width=80, font=FONT_BODY, fg_color=COL_CARD_2, border_width=0)
        entry.insert(0, str(getattr(self.settings, attribute)))
        entry.pack(side="right")
        setattr(self, f"entry_{attribute}", entry)
        if hint:
            ctk.CTkLabel(parent, text=hint, font=FONT_SMALL, text_color=COL_MUTED,
                         wraplength=520, justify="left").pack(anchor="w", padx=18, pady=(2, 0))

    def _text(self, parent, text: str, attribute: str, hint: str = "") -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(14, 0))
        ctk.CTkLabel(row, text=text, font=FONT_BODY, anchor="w").pack(side="left")
        entry = ctk.CTkEntry(row, width=240, font=FONT_BODY, fg_color=COL_CARD_2, border_width=0)
        entry.insert(0, str(getattr(self.settings, attribute)))
        entry.pack(side="right")
        setattr(self, f"entry_{attribute}", entry)
        if hint:
            ctk.CTkLabel(parent, text=hint, font=FONT_SMALL, text_color=COL_MUTED,
                         wraplength=520, justify="left").pack(anchor="w", padx=18, pady=(2, 0))

    # ------------------------------------------------------------------
    def _build_output(self, tab) -> None:
        self._switch(tab, f"A magyar felirat kerüljön bele az MKV-be", "mux_into_mkv",
                     f"A kész felirat „{TRACK_TITLE}” néven, magyar (hun) nyelvcímkével kerül a fájlba. "
                     "A videó és a hang bitre pontosan másolódik, nincs újrakódolás.")
        self._switch(tab, "Legyen ez az alapértelmezett felirat sáv", "set_as_default",
                     "Lejátszáskor automatikusan a magyar felirat indul, a többiről lekerül az "
                     "alapértelmezett jelölés.")
        self._switch(tab, "Az eredeti MKV fájl felülírása", "replace_original",
                     "Bekapcsolva ugyanaz a fájl marad, csak bővül a magyar sávval. Kikapcsolva "
                     "külön „.hu.mkv” fájl készül mellé.")
        self._switch(tab, "Biztonsági másolat az eredetiről (.bak)", "keep_backup",
                     "Felülírás előtt az eredeti fájl átnevezve megmarad. Sok helyet foglal.")
        self._switch(tab, "A külön _magyar_felirat.srt fájl is maradjon meg", "keep_srt_file",
                     "Így az MKV mellett önálló feliratfájlként is megvan a fordítás.")
        self._switch(tab, "Kihagyás, ha már van benne magyar felirat", "skip_if_hungarian_exists",
                     "Kötegelt módban a már kész epizódokat átugorja - így bármikor újraindítható.")
        self._switch(tab, "Almappák bejárása is", "recursive_scan",
                     "Mappás módban az összes almappában megkeresi az MKV fájlokat.")

    def _build_translation(self, tab) -> None:
        self._switch(tab, "Feliratokon átnyúló mondatok egyben fordítása", "merge_sentences",
                     "Ha egy mondat két felirat között törik ketté, a fordító EGYBEN kapja meg, "
                     "és a kész magyar mondatot osztjuk vissza a feliratokra. Ez a legnagyobb "
                     "minőségjavító lépés – enélkül a fordító félmondatokat kap.")
        self._number(tab, "Felirat sor hossza (karakter)", "max_line_length",
                     "A szakmai szabvány 42 karakter. Ennél hosszabb szöveget a program "
                     "automatikusan, mondathatáron tördeli szét.")
        self._number(tab, "Maximális sorszám feliratonként", "max_lines",
                     "Két sor az ajánlott - ennél többet nehéz elolvasni.")
        self._text(tab, "Forrásnyelv", "source_lang",
                   "Hagyd „auto” értéken - a program a sáv nyelvcímkéjéből és az írásjelekből "
                   "ismeri fel. Kézzel megadható: ja, en, de, fr…")
        self._switch(tab, "Gyorsítótár használata", "use_cache",
                     "A már lefordított sorokat nem fordítja le újra. Egy sorozat második "
                     "évadánál is sokat gyorsít.")

    def _build_advanced(self, tab) -> None:
        self._number(tab, "Párhuzamos Microsoft kérések", "bing_workers",
                     "8 a jó alapérték. Túl sok esetén a szolgáltatás átmenetileg korlátoz.")
        self._number(tab, "Párhuzamos Google kérések", "google_workers",
                     "Ugyanez a Google tartalék motorra.")

        ctk.CTkButton(tab, text="🗑  Gyorsítótár ürítése", font=FONT_BODY, height=34,
                      fg_color=COL_CARD_2, hover_color=COL_ERR,
                      command=self._clear_cache).pack(fill="x", padx=18, pady=(24, 0))
        ctk.CTkLabel(tab, text="A gyorsítótár a korábbi fordításokat tárolja. Ürítés után minden "
                              "sor újra le lesz fordítva.", font=FONT_SMALL, text_color=COL_MUTED,
                     wraplength=520, justify="left").pack(anchor="w", padx=18, pady=(4, 0))

    def _build_glossary(self, tab) -> None:
        ctk.CTkLabel(tab, text="Névszótár", font=FONT_CARD, anchor="w").pack(
            anchor="w", padx=18, pady=(14, 2))
        ctk.CTkLabel(
            tab,
            text="Soronként egy bejegyzés, „eredeti = magyar” alakban. Ezeket a program a fordítás "
                 "ELŐTT lecseréli, így a szereplőnevek és a visszatérő kifejezések végig ugyanúgy "
                 "szerepelnek – a fordítók a latin betűs neveket békén hagyják.\n\n"
                 "Példa:\n    田中 = Tanaka\n    Hokage = Hokage\n    onii-chan = bátyó",
            font=FONT_SMALL, text_color=COL_MUTED, wraplength=520, justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 8))

        self.glossary_box = ctk.CTkTextbox(tab, font=FONT_MONO, fg_color=COL_CARD_2,
                                           corner_radius=8, height=260)
        self.glossary_box.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        existing = "\n".join(f"{k} = {v}" for k, v in (self.settings.glossary or {}).items())
        self.glossary_box.insert("1.0", existing)

    # ------------------------------------------------------------------
    def _clear_cache(self) -> None:
        count = self.app.translator.cache.clear()
        messagebox.showinfo("Gyorsítótár", f"{count} tárolt fordítás törölve.", parent=self)

    def _save(self) -> None:
        for attribute in ("mux_into_mkv", "set_as_default", "replace_original", "keep_backup",
                          "keep_srt_file", "skip_if_hungarian_exists", "recursive_scan",
                          "merge_sentences", "use_cache"):
            variable = getattr(self, f"var_{attribute}", None)
            if variable is not None:
                setattr(self.settings, attribute, bool(variable.get()))

        for attribute in ("max_line_length", "max_lines", "bing_workers", "google_workers"):
            entry = getattr(self, f"entry_{attribute}", None)
            if entry is not None:
                try:
                    setattr(self.settings, attribute, int(entry.get().strip()))
                except ValueError:
                    log.warning("Érvénytelen szám a(z) %s mezőben, marad a régi érték.", attribute)

        for attribute in ("source_lang",):
            entry = getattr(self, f"entry_{attribute}", None)
            if entry is not None:
                setattr(self.settings, attribute, entry.get().strip())

        # Névszótár: "eredeti = magyar" soronként.
        glossary: dict[str, str] = {}
        for line in self.glossary_box.get("1.0", "end").split("\n"):
            if "=" not in line:
                continue
            source, target = line.split("=", 1)
            source, target = source.strip(), target.strip()
            if source and target:
                glossary[source] = target
        self.settings.glossary = glossary
        if glossary:
            log.info("Névszótár: %d bejegyzés mentve.", len(glossary))

        self.settings.save()
        log.info("Beállítások elmentve.")
        # A motorokat újra kell építeni, hogy az új értékekkel induljanak.
        self.app.translator._engine_cache.clear()
        self.app._refresh_engine_status()
        self.destroy()


def run() -> None:
    app = App()
    app.mainloop()
