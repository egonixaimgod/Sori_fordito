import customtkinter as ctk
from tkinter import filedialog, messagebox
import os
import sys
import subprocess
import json
import pysrt
from openai import OpenAI
from deep_translator import GoogleTranslator
import time
import threading
import traceback

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

CONFIG_FILE = "aifordito_config.json"

class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("AI MKV Felirat Fordító")
        self.geometry("700x700")

        if getattr(sys, 'frozen', False):
            self.base_path = sys._MEIPASS
        else:
            self.base_path = os.path.dirname(os.path.abspath(__file__))

        self.ffmpeg_path = os.path.join(self.base_path, "ffmpeg.exe")
        self.ffprobe_path = os.path.join(self.base_path, "ffprobe.exe")

        if not os.path.exists(self.ffmpeg_path):
            self.ffmpeg_path = "ffmpeg"
            self.ffprobe_path = "ffprobe"

        self.mkv_path = ""
        self.tracks = []

        # Load API key from config
        self.saved_api_key = ""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    self.saved_api_key = config.get("api_key", "")
            except Exception:
                pass

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(8, weight=1)

        # Engine Selection
        self.engine_label = ctk.CTkLabel(self, text="Fordító Motor:")
        self.engine_label.grid(row=0, column=0, padx=10, pady=10, sticky="w")
        
        self.engine_combo = ctk.CTkComboBox(self, values=["Google Translate (Ingyenes)", "OpenAI (Fizetős API)"], command=self.engine_changed)
        self.engine_combo.grid(row=0, column=1, padx=10, pady=10, sticky="ew")

        # API Key (No masking, prefill if saved)
        self.api_label = ctk.CTkLabel(self, text="OpenAI API Kulcs:")
        self.api_entry = ctk.CTkEntry(self, placeholder_text="Mindenképp adj meg API kulcsot...", width=300)
        self.api_entry.insert(0, self.saved_api_key)
        
        # Hide API key by default since Google Translate is selected
        self.engine_combo.set("Google Translate (Ingyenes)")

        # File selection
        self.file_label = ctk.CTkLabel(self, text="MKV Fájl:")
        self.file_label.grid(row=2, column=0, padx=10, pady=10, sticky="w")
        
        self.file_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.file_frame.grid(row=2, column=1, padx=10, pady=10, sticky="ew")
        self.file_frame.grid_columnconfigure(0, weight=1)
        
        self.file_entry = ctk.CTkEntry(self.file_frame, placeholder_text="Válassz fájlt...", state="readonly")
        self.file_entry.grid(row=0, column=0, sticky="ew")
        
        self.browse_btn = ctk.CTkButton(self.file_frame, text="Tallózás", width=80, command=self.browse_file)
        self.browse_btn.grid(row=0, column=1, padx=(10, 0))

        # Analyze button
        self.analyze_btn = ctk.CTkButton(self, text="🔍 Felirat sávok keresése", command=self.analyze_mkv)
        self.analyze_btn.grid(row=3, column=0, columnspan=2, padx=10, pady=10)

        # Track selection
        self.track_label = ctk.CTkLabel(self, text="Felirat sáv:")
        self.track_label.grid(row=4, column=0, padx=10, pady=10, sticky="w")
        
        self.track_combo = ctk.CTkComboBox(self, values=["Előbb elemezd a fájlt!"], state="readonly")
        self.track_combo.grid(row=4, column=1, padx=10, pady=10, sticky="ew")

        # Process button
        self.process_btn = ctk.CTkButton(self, text="🚀 Kinyerés és Fordítás", command=self.start_processing, fg_color="green", hover_color="darkgreen")
        self.process_btn.grid(row=5, column=0, columnspan=2, padx=10, pady=20)

        # Progress
        self.status_label = ctk.CTkLabel(self, text="Várakozás a beállításokra...")
        self.status_label.grid(row=6, column=0, columnspan=2, padx=10, pady=(10, 0))
        
        self.progress_bar = ctk.CTkProgressBar(self)
        self.progress_bar.grid(row=7, column=0, columnspan=2, padx=10, pady=10, sticky="ew")
        self.progress_bar.set(0)

        # Debug Logger
        self.debug_log = ctk.CTkTextbox(self, height=150)
        self.debug_log.grid(row=8, column=0, columnspan=2, padx=10, pady=10, sticky="nsew")
        self.debug_log.insert("end", "--- Rendszer indítása ---\n")
        self.debug_log.configure(state="disabled")

    def log_debug(self, msg):
        self.debug_log.configure(state="normal")
        self.debug_log.insert("end", str(msg) + "\n")
        self.debug_log.see("end")
        self.debug_log.configure(state="disabled")
        try:
            print(msg)
        except Exception:
            pass

    def engine_changed(self, choice):
        if choice == "OpenAI (Fizetős API)":
            self.api_label.grid(row=1, column=0, padx=10, pady=10, sticky="w")
            self.api_entry.grid(row=1, column=1, padx=10, pady=10, sticky="ew")
        else:
            self.api_label.grid_forget()
            self.api_entry.grid_forget()

    def browse_file(self):
        filename = filedialog.askopenfilename(title="Válassz MKV fájlt", filetypes=[("MKV Video", "*.mkv"), ("All Files", "*.*")])
        if filename:
            self.mkv_path = filename
            self.file_entry.configure(state="normal")
            self.file_entry.delete(0, "end")
            self.file_entry.insert(0, filename)
            self.file_entry.configure(state="readonly")
            self.log_debug(f"Kiválasztott fájl: {self.mkv_path}")

    def log_status(self, msg):
        self.status_label.configure(text=msg)
        self.log_debug(f"[STATUS] {msg}")
        self.update_idletasks()

    def analyze_mkv(self):
        if not self.mkv_path or not os.path.exists(self.mkv_path):
            messagebox.showerror("Hiba", "Kérlek válassz ki egy létező MKV fájlt!")
            return

        self.log_status("Sávok elemzése...")
        self.analyze_btn.configure(state="disabled")
        threading.Thread(target=self._analyze_thread, daemon=True).start()

    def _analyze_thread(self):
        creationflags = 0x08000000 if os.name == 'nt' else 0
        command = [
            self.ffprobe_path, "-v", "error", "-show_streams",
            "-select_streams", "s", "-of", "json", self.mkv_path
        ]
        self.log_debug(f"FFprobe parancs: {' '.join(command)}")
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True, creationflags=creationflags)
            self.log_debug(f"FFprobe kimenet beolvasva.")
                
            info = json.loads(result.stdout)
            self.tracks = info.get("streams", [])
            
            if not self.tracks:
                self.log_status("Kész, de nem találtam felirat sávot!")
                self.track_combo.configure(values=["Nincs felirat sáv!"])
            else:
                track_names = []
                for t in self.tracks:
                    idx = t.get("index")
                    codec = t.get("codec_name", "ismeretlen")
                    
                    # Convert all tags to lowercase for case-insensitive lookup
                    tags = {k.lower(): v for k, v in t.get("tags", {}).items()}
                    lang = tags.get("language", "ismeretlen nyelv")
                    title = tags.get("title", "")
                    
                    if title:
                        name = f"Sáv {idx}: {lang.upper()} - {title} ({codec})"
                    else:
                        name = f"Sáv {idx}: {lang.upper()} ({codec})"
                        
                    track_names.append(name)
                    self.log_debug(f"Talált sáv: {name}")
                
                self.track_combo.configure(values=track_names)
                self.track_combo.set(track_names[0])
                self.log_status(f"{len(track_names)} felirat sáv találva.")
        except subprocess.CalledProcessError as cpe:
            self.log_status("Hiba történt az elemzés során!")
            self.log_debug(f"FFprobe futtatási hiba:\n{cpe.stderr}")
        except Exception as e:
            self.log_status("Kritikus hiba történt az elemzés során!")
            self.log_debug(traceback.format_exc())
            messagebox.showerror("Hiba", f"Nem sikerült beolvasni az MKV fájlt:\n{e}")
        finally:
            self.analyze_btn.configure(state="normal")

    def save_api_key(self, api_key):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"api_key": api_key}, f)
            self.log_debug("API kulcs sikeresen elmentve a config.json-be.")
        except Exception as e:
            self.log_debug(f"Nem sikerült menteni az API kulcsot: {e}")

    def start_processing(self):
        engine = self.engine_combo.get()
        api_key = self.api_entry.get().strip()
        
        if engine == "OpenAI (Fizetős API)":
            if not api_key:
                messagebox.showerror("Hiba", "Kérlek add meg az OpenAI API kulcsodat!")
                return
            self.save_api_key(api_key)
            
        if not self.tracks:
            messagebox.showerror("Hiba", "Előbb elemezd az MKV fájlt és válassz egy sávot!")
            return
            
        selected_text = self.track_combo.get()
        selected_idx = None
        for t in self.tracks:
            idx = t.get("index")
            if f"Sáv {idx}:" in selected_text:
                selected_idx = idx
                break
                
        if selected_idx is None:
            self.log_debug("Hiba: Nem sikerült kiválasztani a sáv indexét.")
            return

        self.process_btn.configure(state="disabled")
        threading.Thread(target=self._process_thread, args=(engine, api_key, selected_idx), daemon=True).start()

    def _process_thread(self, engine, api_key, stream_index):
        try:
            base_dir = os.path.dirname(self.mkv_path)
            base_name = os.path.basename(self.mkv_path).rsplit(".", 1)[0]
            extracted_srt = os.path.join(base_dir, f"{base_name}_extracted.srt")
            output_path = os.path.join(base_dir, f"{base_name}_HU.srt")
            
            self.log_status(f"Felirat kinyerése a {stream_index}. sávból...")
            creationflags = 0x08000000 if os.name == 'nt' else 0
            
            command = [
                self.ffmpeg_path, "-y", "-i", self.mkv_path, "-map", f"0:{stream_index}", 
                "-c:s", "srt", extracted_srt
            ]
            self.log_debug(f"FFmpeg parancs: {' '.join(command)}")
            
            result = subprocess.run(command, capture_output=True, text=True, creationflags=creationflags)
            if result.stderr:
                self.log_debug(f"FFmpeg kimenet (stderr, gyakran csak info):\n{result.stderr}")
            
            if not os.path.exists(extracted_srt):
                self.log_debug("Hiba: Az FFmpeg nem hozta létre a kimeneti SRT fájlt.")
                messagebox.showerror("Hiba", "Az FFmpeg nem tudta kinyerni a feliratot.")
                return

            self.log_status(f"Felirat kinyerve. Kezdődik a fordítás ({engine})...")
            
            try:
                subs = pysrt.open(extracted_srt, encoding='utf-8')
            except Exception:
                self.log_debug("Hiba UTF-8 beolvasáskor, újrapróbálás cp1250-nel.")
                subs = pysrt.open(extracted_srt, encoding='cp1250')
                
            # Chunking strategy: 50 lines at a time.
            chunk_size = 50
            total_subs = len(subs)
            self.log_debug(f"Összes felirat sor: {total_subs}")
            
            if total_subs == 0:
                self.log_status("A kinyert felirat üres!")
                return
            
            client = None
            translator = None
            if engine == "OpenAI (Fizetős API)":
                client = OpenAI(api_key=api_key)
            else:
                translator = GoogleTranslator(source='auto', target='hu')
            
            for i in range(0, total_subs, chunk_size):
                chunk = subs[i:i + chunk_size]
                original_texts = [sub.text.replace('\n', ' ') for sub in chunk]
                
                self.log_status(f"Fordítás: {i}/{total_subs} felirat feldolgozva...")
                
                translated_blocks = []
                
                if engine == "OpenAI (Fizetős API)":
                    system_prompt = (
                        "Te egy professzionális sorozat- és filmfelirat fordító vagy. "
                        "A feladatod a kapott felirat pontos, természetes magyarra fordítása. "
                        "A kapott felirat sorait SZIGORÚAN tarts meg: ahány sor van a bemenetben, "
                        "annyi sornak kell lennie a kimenetben is. "
                        "Ne adj semmilyen extra magyarázatot, csak a lefordított szöveget add vissza, "
                        "soronként az eredetivel megegyezően!"
                    )
                    text_to_translate = "\n".join(original_texts)
                    
                    try:
                        self.log_debug(f"Kérés küldése az OpenAI-nak ({len(original_texts)} sor egyben)...")
                        response = client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": f"Kérlek fordítsd le ezeket a feliratokat:\n\n{text_to_translate}"}
                            ],
                            temperature=0.3
                        )
                        translated_text = response.choices[0].message.content.strip()
                        translated_blocks = translated_text.split('\n')
                    except Exception as e:
                        self.log_debug(f"OpenAI hiba: {e}")
                else:
                    try:
                        self.log_debug(f"Kérés küldése a Google Translate-nek ({len(original_texts)} sor egyben)...")
                        text_to_translate = "\n".join(original_texts)
                        translated_text = translator.translate(text_to_translate)
                        translated_blocks = translated_text.split('\n')
                        
                    except Exception as e:
                        self.log_debug(f"Google Translate hiba: {e}")
                        translated_blocks = original_texts
                
                if len(translated_blocks) != len(original_texts):
                    self.log_debug(f"Fordítási sorok eltérnek (eredeti: {len(original_texts)}, fordított: {len(translated_blocks)}). Javítás...")
                    while len(translated_blocks) < len(original_texts):
                        translated_blocks.append("")
                    translated_blocks = translated_blocks[:len(original_texts)]
                    
                for j, sub in enumerate(chunk):
                    if j < len(translated_blocks):
                        sub.text = translated_blocks[j]
                
                self.progress_bar.set(min((i + chunk_size) / total_subs, 1.0))
                
            subs.save(output_path, encoding='utf-8')
            
            try:
                os.remove(extracted_srt)
            except:
                pass
                
            self.log_status(f"KÉSZ! Fájl mentve ide:\n{output_path}")
            self.log_debug(f"Sikeres mentés: {output_path}")
            messagebox.showinfo("Siker", f"A fordítás elkészült és elmentve ide:\n{output_path}")
            
        except Exception as e:
            self.log_status("Kritikus hiba történt a feldolgozás során!")
            self.log_debug(traceback.format_exc())
            messagebox.showerror("Hiba", f"Hiba történt:\n{e}")
        finally:
            self.process_btn.configure(state="normal")
            self.progress_bar.set(0)

if __name__ == "__main__":
    app = App()
    app.mainloop()