import streamlit as st
import subprocess
import json
import os
import pysrt
from openai import OpenAI
import time

# --- Oldal beállítása ---
st.set_page_config(page_title="AI Felirat Fordító", page_icon="🎬", layout="centered")

st.title("🎬 AI MKV Felirat Fordító")
st.markdown("Ezzel az alkalmazással MKV fájlokból nyerhetsz ki feliratokat, amelyeket az OpenAI AI modellje magyarra fordít.")

# --- Session State ---
if 'api_key' not in st.session_state:
    st.session_state.api_key = ''

# --- Függvények ---
def get_subtitle_tracks(mkv_path):
    """Lekérdezi az MKV fájlban lévő felirat sávokat ffprobe segítségével"""
    command = [
        "ffprobe", "-v", "error", "-show_entries", "stream=index,codec_name,tags",
        "-select_streams", "s", "-of", "json", mkv_path
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
        return info.get("streams", [])
    except Exception as e:
        st.error(f"Hiba a fájl beolvasásakor: {e}")
        return []

def extract_subtitle(mkv_path, stream_index, output_srt_path):
    """Kinyeri az adott indexű feliratot az MKV-ból SRT formátumba"""
    command = [
        "ffmpeg", "-y", "-i", mkv_path, "-map", f"0:{stream_index}", 
        output_srt_path
    ]
    try:
        subprocess.run(command, capture_output=True, text=True, check=True)
        return True
    except subprocess.CalledProcessError as e:
        st.error(f"Hiba a kinyerés során: {e.stderr}")
        return False

def translate_text_blocks(client, text_blocks):
    """A felirat blokk fordítása OpenAI használatával"""
    system_prompt = (
        "Te egy professzionális sorozat- és filmfelirat fordító vagy. "
        "A feladatod a kapott felirat pontos, természetes magyarra fordítása. "
        "A kapott felirat sorait SZIGORÚAN tarts meg: ahány sor van a bemenetben, "
        "annyi sornak kell lennie a kimenetben is. "
        "Ne adj semmilyen extra magyarázatot, csak a lefordított szöveget add vissza, "
        "soronként az eredetivel megegyezően!"
    )
    
    text_to_translate = "\n".join(text_blocks)
    
    try:
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
        
        # Ha a sorok száma nem egyezik, ez egy egyszerű fallback
        if len(translated_blocks) != len(text_blocks):
            # Próbáljuk meg legalább kipótolni, hogy ne omoljon össze
            while len(translated_blocks) < len(text_blocks):
                translated_blocks.append("")
            translated_blocks = translated_blocks[:len(text_blocks)]
            
        return translated_blocks
    except Exception as e:
        st.error(f"API Hiba: {e}")
        return text_blocks # Ha hiba van, visszaadjuk az eredetit


def process_and_translate_srt(srt_path, api_key):
    """Beolvassa az SRT-t, és blokkokban lefordítja"""
    subs = pysrt.open(srt_path)
    client = OpenAI(api_key=api_key)
    
    chunk_size = 30 # Egyszerre 30 felirat (nem túl sok, hogy ne keveredjen meg az AI)
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    total_subs = len(subs)
    
    for i in range(0, total_subs, chunk_size):
        chunk = subs[i:i + chunk_size]
        original_texts = [sub.text.replace('\n', ' ') for sub in chunk] # Sorok egybemosása, hogy 1 felirat 1 sor legyen a promptban
        
        status_text.text(f"Fordítás folyamatban: {i}/{total_subs} felirat feldolgozva...")
        
        translated_texts = translate_text_blocks(client, original_texts)
        
        for j, sub in enumerate(chunk):
            if j < len(translated_texts):
                sub.text = translated_texts[j]
        
        # Progress update
        progress = min((i + chunk_size) / total_subs, 1.0)
        progress_bar.progress(progress)
        
        time.sleep(0.5) # Kis késleltetés a Rate Limit miatt
    
    status_text.text("Fordítás sikeresen befejeződött!")
    
    # Mentés új fájlba
    output_path = srt_path.replace(".srt", "_HU.srt")
    subs.save(output_path, encoding='utf-8')
    return output_path


# --- UI Logika ---

with st.sidebar:
    st.header("⚙️ Beállítások")
    st.session_state.api_key = st.text_input("OpenAI API Kulcs", value=st.session_state.api_key, type="password", help="Szükséges a fordításhoz.")
    st.markdown("[API kulcs igénylése itt](https://platform.openai.com/api-keys)")

mkv_path = st.text_input("📁 MKV fájl elérési útja", placeholder="Pl: C:\\Filmek\\anime_ep01.mkv")

if mkv_path:
    if not os.path.exists(mkv_path):
        st.error("A megadott fájl nem található! Kérlek ellenőrizd az elérési utat.")
    else:
        st.success("Fájl sikeresen megtalálva!")
        
        if st.button("🔍 Felirat sávok keresése"):
            with st.spinner("Sávok elemzése..."):
                tracks = get_subtitle_tracks(mkv_path)
                st.session_state.tracks = tracks
        
        if 'tracks' in st.session_state:
            if not st.session_state.tracks:
                st.warning("Nem találtam beépített felirat sávot az MKV fájlban.")
            else:
                track_options = {}
                for t in st.session_state.tracks:
                    idx = t.get("index")
                    lang = t.get("tags", {}).get("language", "ismeretlen")
                    title = t.get("tags", {}).get("title", "")
                    track_options[idx] = f"Sáv {idx}: {lang.upper()} {f'({title})' if title else ''}"
                
                selected_track = st.selectbox("Válaszd ki a kinyerni kívánt feliratot", options=list(track_options.keys()), format_func=lambda x: track_options[x])
                
                if st.button("🚀 Kinyerés és Fordítás"):
                    if not st.session_state.api_key:
                        st.error("Kérlek add meg az OpenAI API kulcsodat a bal oldali sávban!")
                    else:
                        base_dir = os.path.dirname(mkv_path)
                        base_name = os.path.basename(mkv_path).rsplit(".", 1)[0]
                        extracted_srt = os.path.join(base_dir, f"{base_name}_extracted.srt")
                        
                        st.info(f"⏳ Felirat kinyerése a(z) {selected_track}. sávból...")
                        if extract_subtitle(mkv_path, selected_track, extracted_srt):
                            st.success("✅ Felirat sikeresen kinyerve! Kezdődik a fordítás...")
                            
                            translated_srt = process_and_translate_srt(extracted_srt, st.session_state.api_key)
                            
                            with open(translated_srt, "rb") as file:
                                btn = st.download_button(
                                    label="📥 Kész Magyar Felirat (SRT) Letöltése",
                                    data=file,
                                    file_name=os.path.basename(translated_srt),
                                    mime="text/plain"
                                )
                            
                            # Opcionális takarítás
                            try:
                                os.remove(extracted_srt)
                            except:
                                pass
