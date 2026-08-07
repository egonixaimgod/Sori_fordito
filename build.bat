@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Magyar Felirat Fordito - build
cd /d "%~dp0"

echo.
echo ==================================================================
echo   MAGYAR FELIRAT FORDITO - onallo exe keszitese
echo ==================================================================
echo.

set "EXE_NAME=MagyarFeliratFordito.exe"

REM ------------------------------------------------------------------
REM  1. Fut-e meg a program? (a legygakoribb build-hiba oka)
REM ------------------------------------------------------------------
tasklist /FI "IMAGENAME eq %EXE_NAME%" 2>nul | find /I "%EXE_NAME%" >nul
if not errorlevel 1 (
    echo   [HIBA] A %EXE_NAME% jelenleg fut, ezert nem lehet felulirni.
    echo     Zard be a programot, aztan inditsd ujra ezt a scriptet.
    echo.
    pause
    exit /b 1
)

REM ------------------------------------------------------------------
REM  2. Python
REM ------------------------------------------------------------------
echo [1/6] Python keresese...
set "PY=python"
%PY% --version >nul 2>&1
if errorlevel 1 (
    set "PY=py -3"
    !PY! --version >nul 2>&1
    if errorlevel 1 (
        echo.
        echo   [HIBA] Nem talalok Pythont a gepen.
        echo     Telepitsd innen: https://www.python.org/downloads/
        echo     Telepiteskor pipald be az "Add Python to PATH" opciot!
        echo.
        pause
        exit /b 1
    )
)
for /f "tokens=*" %%V in ('%PY% --version 2^>^&1') do echo       %%V

REM ------------------------------------------------------------------
REM  3. Fuggosegek
REM ------------------------------------------------------------------
echo [2/6] Fuggosegek ellenorzese...

%PY% -m pip show customtkinter >nul 2>&1
if errorlevel 1 (
    echo       customtkinter telepitese...
    %PY% -m pip install --quiet --upgrade customtkinter
    if errorlevel 1 goto :pip_hiba
) else (
    echo       customtkinter - megvan
)

%PY% -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo       pyinstaller telepitese...
    %PY% -m pip install --quiet --upgrade pyinstaller
    if errorlevel 1 goto :pip_hiba
) else (
    echo       pyinstaller - megvan
)

REM ------------------------------------------------------------------
REM  4. ffmpeg / ffprobe - ezek kerulnek bele az exe-be
REM ------------------------------------------------------------------
echo [3/6] ffmpeg ellenorzese...
if not exist "ffmpeg.exe" goto :ffmpeg_letoltes
if not exist "ffprobe.exe" goto :ffmpeg_letoltes
echo       ffmpeg.exe es ffprobe.exe - megvan
goto :ffmpeg_kesz

:ffmpeg_letoltes
echo       hianyzik, letoltes indul (kb. 100 MB)...
%PY% download_ffmpeg.py
if not exist "ffmpeg.exe" (
    echo.
    echo   [HIBA] Az ffmpeg letoltese nem sikerult.
    echo     Toltsd le kezzel: https://www.gyan.dev/ffmpeg/builds/
    echo     Az ffmpeg.exe es ffprobe.exe keruljon ebbe a mappaba.
    echo.
    pause
    exit /b 1
)

:ffmpeg_kesz

REM ------------------------------------------------------------------
REM  5. Gyors ellenorzes: importalhato-e minden modul?
REM ------------------------------------------------------------------
echo [4/6] Kod ellenorzese...
%PY% -c "import fordito.gui, fordito.pipeline, fordito.translator, fordito.media, fordito.subtitles, fordito.engines" 2>nul
if errorlevel 1 (
    echo.
    echo   [HIBA] A program moduljai nem importalhatok - a build ertelmetlen lenne.
    echo     Reszletek:
    %PY% -c "import fordito.gui, fordito.pipeline, fordito.translator, fordito.media, fordito.subtitles, fordito.engines"
    echo.
    pause
    exit /b 1
)
echo       minden modul rendben

REM ------------------------------------------------------------------
REM  6. Takaritas es build
REM ------------------------------------------------------------------
echo [5/6] Korabbi build torlese...
if exist "build" rmdir /s /q "build" 2>nul
if exist "dist\%EXE_NAME%" del /f /q "dist\%EXE_NAME%" 2>nul
if exist "__pycache__" rmdir /s /q "__pycache__" 2>nul
if exist "fordito\__pycache__" rmdir /s /q "fordito\__pycache__" 2>nul
if exist "fordito\engines\__pycache__" rmdir /s /q "fordito\engines\__pycache__" 2>nul

echo [6/6] Exe forditasa... (ez 1-3 percig tart, turelem)
echo.
%PY% -m PyInstaller --noconfirm --clean AIFordito.spec
if errorlevel 1 goto :build_hiba

if not exist "dist\%EXE_NAME%" goto :build_hiba

REM ------------------------------------------------------------------
REM  Kesz
REM ------------------------------------------------------------------
for %%A in ("dist\%EXE_NAME%") do set "MERET=%%~zA"
set /a MERET_MB=!MERET!/1048576

echo.
echo ==================================================================
echo   KESZ!
echo ==================================================================
echo.
echo   Fajl:   %CD%\dist\%EXE_NAME%
echo   Meret:  !MERET_MB! MB
echo.
echo   Ez egy onallo fajl: az ffmpeg is benne van, semmit nem kell
echo   melle telepiteni. Barhova masolhato, csak internet kell neki.
echo.
echo   A program a maga melle irja ezeket:
echo     fordito_debug.log          - teljes naplo
echo     fordito_beallitasok.json   - beallitasok
echo     fordito_cache.sqlite       - forditas gyorsitotar
echo.

explorer "%CD%\dist"

:vege
echo.
exit /b 0

REM ------------------------------------------------------------------
REM  Hibaagak
REM ------------------------------------------------------------------
:pip_hiba
echo.
echo   [HIBA] A csomagok telepitese nem sikerult.
echo     Ellenorizd az internetkapcsolatot, vagy futtasd kezzel:
echo     %PY% -m pip install customtkinter pyinstaller
echo.
pause
exit /b 1

:build_hiba
echo.
echo ==================================================================
echo   [HIBA] A BUILD NEM SIKERULT
echo ==================================================================
echo.
echo   Nezd meg a fenti PyInstaller uzeneteket.
echo   Gyakori okok:
echo     - a program meg fut (zard be, es probald ujra)
echo     - a viruskereso blokkolja a dist mappat
echo     - keves a szabad hely a lemezen (kb. 1 GB kell a buildhez)
echo.
pause
exit /b 1
