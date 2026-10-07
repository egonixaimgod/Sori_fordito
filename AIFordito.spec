# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller leírás - önálló .exe készítése.

Használat:  pyinstaller AIFordito.spec
Az eredmény: dist/MagyarFeliratFordito.exe (az ffmpeg is benne van).
"""
from PyInstaller.utils.hooks import collect_all

# Az ikon a futó ablakhoz is kell (a program a resource_dir()-ből tölti be).
datas = [('ffmpeg.exe', '.'), ('ffprobe.exe', '.'), ('icon_fordito.ico', '.')]
binaries = []
hiddenimports = ['fordito', 'fordito.engines']

# A felülethez kellenek a csomagolt témafájlok.
tmp_ret = collect_all('customtkinter')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Nem kell: a fordítás és a hálózat a beépített modulokkal megy.
    excludes=['openai', 'deep_translator', 'pysrt', 'streamlit', 'numpy',
              'pandas', 'matplotlib', 'PIL.ImageQt', 'test', 'unittest'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MagyarFeliratFordito',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['ffmpeg.exe', 'ffprobe.exe'],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # A build számot a bump_build.py írja bele minden kiadásnál.
    version='version_info.txt',
    icon=['icon_fordito.ico'],
)
