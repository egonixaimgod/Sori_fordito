import urllib.request
import zipfile
import os
import shutil

url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
zip_path = "ffmpeg.zip"

if not os.path.exists("ffmpeg.exe"):
    print("Downloading FFmpeg...")
    urllib.request.urlretrieve(url, zip_path)
    
    print("Extracting FFmpeg...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        for file in zip_ref.namelist():
            if file.endswith("ffmpeg.exe") or file.endswith("ffprobe.exe"):
                extracted_path = zip_ref.extract(file, ".")
                target_path = os.path.basename(file)
                if os.path.exists(target_path):
                    os.remove(target_path)
                os.rename(extracted_path, target_path)
    
    # Cleanup extracted folder
    try:
        shutil.rmtree(zip_ref.namelist()[0].split('/')[0])
        os.remove(zip_path)
    except:
        pass

print("FFmpeg and FFprobe are ready.")
