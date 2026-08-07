@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Magyar Felirat Fordito - Rebuild ^& GitHub Push
cd /d "%~dp0"

echo ==========================================
echo    Magyar Felirat Fordito
echo    Auto Rebuild ^& GitHub Push
echo ==========================================
echo.

set "EXE_NAME=MagyarFeliratFordito.exe"
set "GITHUB_LIMIT=104857600"

REM ==================================================================
REM  [1/4] Elozetes ellenorzesek
REM ==================================================================
echo [1/4] Ellenorzesek...

tasklist /FI "IMAGENAME eq %EXE_NAME%" 2>nul | find /I "%EXE_NAME%" >nul
if not errorlevel 1 (
    echo.
    echo [!] A %EXE_NAME% jelenleg fut, ezert nem lehet felulirni.
    echo     Zard be a programot, es inditsd ujra ezt a scriptet.
    echo.
    pause
    exit /b 1
)

set "PY=python"
%PY% --version >nul 2>&1
if errorlevel 1 (
    set "PY=py -3"
    !PY! --version >nul 2>&1
    if errorlevel 1 (
        echo.
        echo [!] Nem talalok Pythont. Telepitsd: https://www.python.org/downloads/
        echo     Telepiteskor pipald be az "Add Python to PATH" opciot!
        echo.
        pause
        exit /b 1
    )
)

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo.
    echo [!] Ez a mappa nem git repo, igy nincs hova pusholni.
    echo     Ha csak buildelni akarsz, hasznald a build.bat-ot.
    echo.
    pause
    exit /b 1
)

git remote get-url origin >nul 2>&1
if errorlevel 1 (
    echo.
    echo [!] Nincs beallitva "origin" tavoli repo.
    echo     Allitsd be igy:  git remote add origin ^<github-url^>
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%R in ('git remote get-url origin') do set "REPO=%%R"
for /f "tokens=*" %%B in ('git branch --show-current') do set "AG=%%B"
echo       repo:   !REPO!
echo       branch: !AG!

%PY% -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo       pyinstaller telepitese...
    %PY% -m pip install --quiet --upgrade pyinstaller
    if errorlevel 1 goto :pip_hiba
)
%PY% -m pip show customtkinter >nul 2>&1
if errorlevel 1 (
    echo       customtkinter telepitese...
    %PY% -m pip install --quiet --upgrade customtkinter
    if errorlevel 1 goto :pip_hiba
)

if not exist "ffmpeg.exe" goto :ffmpeg_letoltes
if not exist "ffprobe.exe" goto :ffmpeg_letoltes
goto :ffmpeg_kesz
:ffmpeg_letoltes
echo       ffmpeg hianyzik, letoltes...
%PY% download_ffmpeg.py
if not exist "ffmpeg.exe" (
    echo.
    echo [!] Az ffmpeg letoltese nem sikerult.
    echo.
    pause
    exit /b 1
)
:ffmpeg_kesz

%PY% -c "import fordito.gui, fordito.pipeline, fordito.translator, fordito.media, fordito.subtitles, fordito.engines" 2>nul
if errorlevel 1 (
    echo.
    echo [!] A program moduljai nem importalhatok - a build ertelmetlen lenne.
    %PY% -c "import fordito.gui, fordito.pipeline, fordito.translator, fordito.media, fordito.subtitles, fordito.engines"
    echo.
    pause
    exit /b 1
)
echo       minden rendben

REM ==================================================================
REM  [2/4] Build
REM ==================================================================
echo.
echo [2/4] Program leforditasa (PyInstaller)... 1-3 perc
echo.

if exist "build" rmdir /s /q "build" 2>nul
if exist "dist\%EXE_NAME%" del /f /q "dist\%EXE_NAME%" 2>nul

%PY% -m PyInstaller --noconfirm --clean AIFordito.spec
if %ERRORLEVEL% neq 0 (
    echo.
    echo [!] Hiba a build soran! Megszakitjuk a folyamatot.
    pause
    exit /b %ERRORLEVEL%
)
if not exist "dist\%EXE_NAME%" (
    echo.
    echo [!] A build lefutott, de nincs kimeneti exe. Megszakitjuk.
    pause
    exit /b 1
)

for %%A in ("dist\%EXE_NAME%") do set "MERET=%%~zA"
set /a MERET_MB=!MERET!/1048576
echo.
echo       kesz: dist\%EXE_NAME%  (!MERET_MB! MB)

REM ==================================================================
REM  [3/4] Commit uzenet
REM ==================================================================
echo.
echo [3/4] Valtozasok...
git add -A >nul 2>&1

git diff --cached --quiet
if not errorlevel 1 (
    set "NINCS_VALTOZAS=1"
    echo       a forraskodban nincs valtozas
) else (
    set "NINCS_VALTOZAS="
    git diff --cached --stat | more
)

echo.
set "UZENET="
set /p "UZENET=Commit uzenet (Enter = alapertelmezett): "
if "!UZENET!"=="" set "UZENET=Release: automatikus build"

REM --- Az exe csak akkor mehet a repoba, ha belefer a GitHub limitbe ---
set "EXE_MEGY="
if !MERET! LSS %GITHUB_LIMIT% (
    set "EXE_MEGY=1"
    git add -f "dist/%EXE_NAME%"
    echo       az exe is megy a repoba (!MERET_MB! MB)
) else (
    echo.
    echo       [!] Az exe !MERET_MB! MB, a GitHub limitje 100 MB.
    echo           Ezert MOST CSAK A FORRASKOD megy fel - az exe kimarad,
    echo           kulonben a push visszautasitasra kerulne.
    echo           Az exe igy is ott van a dist mappaban, hasznalhato.
)

git diff --cached --quiet
if not errorlevel 1 (
    echo.
    echo       Nincs mit commitolni - a repo mar naprakesz.
    goto :vege_nincs_valtozas
)

REM ==================================================================
REM  [4/4] Commit es push
REM ==================================================================
echo.
echo [4/4] Feltoltes a GitHubra...
git commit -m "!UZENET!"
if %ERRORLEVEL% neq 0 (
    echo.
    echo [!] A commit nem sikerult.
    pause
    exit /b 1
)

git push
if %ERRORLEVEL% neq 0 (
    echo.
    echo [!] A push nem sikerult.
    echo     Gyakori okok:
    echo       - nincs bejelentkezve a git ^(allitsd be a hitelesitest^)
    echo       - a tavoli agban ujabb commit van: futtasd elobb: git pull --rebase
    echo       - 100 MB feletti fajl van a commitban
    echo.
    pause
    exit /b 1
)

echo.
echo ==========================================
echo    SIKERES KIADAS
echo ==========================================
echo.
echo   Commit:  !UZENET!
echo   Repo:    !REPO!
echo   Branch:  !AG!
if defined EXE_MEGY (
    echo   Exe:     feltoltve ^(!MERET_MB! MB^)
) else (
    echo   Exe:     kimaradt - tul nagy a GitHubnak ^(!MERET_MB! MB^)
    echo            Ha fel akarod tenni, csinalj belole Release-t:
    echo            https://github.com/egonixaimgod/googleTranslate_sori_fordito/releases/new
)
echo.
pause
exit /b 0

:vege_nincs_valtozas
echo.
echo ==========================================
echo    Nem volt mit feltolteni
echo ==========================================
echo.
pause
exit /b 0

:pip_hiba
echo.
echo [!] A csomagok telepitese nem sikerult.
echo     Probald kezzel:  %PY% -m pip install customtkinter pyinstaller
echo.
pause
exit /b 1
