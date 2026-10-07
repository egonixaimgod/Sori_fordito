@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Magyar Felirat Fordito - Rebuild ^& Release
cd /d "%~dp0"

set "EXE_NAME=MagyarFeliratFordito.exe"
set "APP_CIM=Magyar Felirat Fordito"

echo ==========================================
echo    %APP_CIM%
echo    Auto Rebuild ^& Release
echo ==========================================
echo.

REM ==================================================================
REM  Elozetes ellenorzesek - minden bukjon el a build ELOTT
REM ==================================================================
echo [0/4] Ellenorzesek...

REM --- Fut-e meg a program? Ha igen, a dist\*.exe zarolva van, es a
REM     PyInstaller nem tudja felulirni. ---
tasklist /FI "IMAGENAME eq %EXE_NAME%" 2>nul | find /I "%EXE_NAME%" >nul
if not errorlevel 1 (
    echo [FIGYELEM] A %EXE_NAME% fut, ezert a dist mappa nem irhato felul.
    set /p VALASZ="Bezarjam most? [i/n] "
    if /i "!VALASZ!"=="i" (
        taskkill /f /im "%EXE_NAME%" >nul 2>&1
        timeout /t 2 /nobreak >nul
        echo       Bezarva.
    ) else (
        echo Zard be a programot, es inditsd ujra ezt a szkriptet.
        pause
        exit /b 1
    )
)

set "PY=python"
%PY% --version >nul 2>&1
if errorlevel 1 (
    set "PY=py -3"
    !PY! --version >nul 2>&1
    if errorlevel 1 (
        echo.
        echo   [HIBA] Nem talalok Pythont a gepen.
        echo          Telepitsd: https://www.python.org/downloads/
        echo          Telepiteskor pipald be az "Add Python to PATH" opciot.
        echo.
        pause
        exit /b 1
    )
)

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [HIBA] Ez a mappa nem git repo, igy nincs hova pusholni.
    echo.
    pause
    exit /b 1
)

git remote get-url origin >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [HIBA] Nincs beallitva "origin" tavoli repo.
    echo          Allitsd be igy:  git remote add origin ^<github-url^>
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%R in ('git remote get-url origin') do set "REPO=%%R"
for /f "tokens=*" %%B in ('git branch --show-current') do set "AG=%%B"
echo       repo:   !REPO!
echo       branch: !AG!

echo       tavoli repo elerese...
git ls-remote origin >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [HIBA] A tavoli repo nem erheto el: !REPO!
    echo          A build el sem indult, semmi nem veszett el.
    echo.
    pause
    exit /b 1
)

REM --- A kiadashoz a gh kell. Jobb most kiderulni, mint 2 perc build utan. ---
set "GH_OK=1"
where gh >nul 2>&1
if errorlevel 1 (
    set "GH_OK=0"
    echo       [FIGYELEM] A "gh" parancs nem talalhato - a GitHub kiadas KI FOG MARADNI.
    echo                  Telepites: winget install GitHub.cli   majd:  gh auth login
) else (
    gh auth status >nul 2>&1
    if errorlevel 1 (
        set "GH_OK=0"
        echo       [FIGYELEM] A "gh" nincs bejelentkezve - a GitHub kiadas KI FOG MARADNI.
        echo                  Javitas:  gh auth login
    )
)
if "!GH_OK!"=="0" (
    set /p FOLYT="Folytassam kiadas nelkul, csak build + push? [i/n] "
    if /i not "!FOLYT!"=="i" (
        echo Megszakitva, semmi nem valtozott.
        pause
        exit /b 1
    )
)

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
    echo   [HIBA] Az ffmpeg letoltese nem sikerult.
    echo.
    pause
    exit /b 1
)
:ffmpeg_kesz

%PY% -c "import fordito.gui, fordito.pipeline, fordito.translator, fordito.media, fordito.subtitles, fordito.engines" 2>nul
if errorlevel 1 (
    echo.
    echo   [HIBA] A program moduljai nem importalhatok - a build ertelmetlen lenne.
    echo          Reszletek:
    %PY% -c "import fordito.gui, fordito.pipeline, fordito.translator, fordito.media, fordito.subtitles, fordito.engines"
    echo.
    pause
    exit /b 1
)
echo       minden rendben

REM ==================================================================
REM  [1/4] Build szam novelese
REM ==================================================================
echo.
echo [1/4] Build szam novelese...
%PY% bump_build.py > temp_build.txt
if errorlevel 1 (
    del temp_build.txt 2>nul
    echo [!] A build szam noveles nem sikerult.
    pause
    exit /b 1
)
set /p NEW_BUILD=<temp_build.txt
del temp_build.txt
echo       Uj build: !NEW_BUILD!

REM ==================================================================
REM  [2/4] Build
REM ==================================================================
echo.
echo [2/4] Program leforditasa (PyInstaller)... 1-3 perc
echo.

REM A dist mappat NEM toroljuk egeszben: ott van a program naploja, cache-e es beallitasai.
if exist "%~dp0build\" rmdir /s /q "%~dp0build" 2>nul
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
    echo [!] A build lefutott, de a dist\%EXE_NAME% nem jott letre.
    pause
    exit /b 1
)

for %%A in ("dist\%EXE_NAME%") do set "MERET=%%~zA"
set /a MERET_MB=!MERET!/1048576
echo.
echo       kesz: dist\%EXE_NAME%  ^(!MERET_MB! MB^)

if exist "%~dp0build\" rmdir /s /q "%~dp0build" 2>nul
if exist "%~dp0fordito\__pycache__\" rmdir /s /q "%~dp0fordito\__pycache__" 2>nul
if exist "%~dp0fordito\engines\__pycache__\" rmdir /s /q "%~dp0fordito\engines\__pycache__" 2>nul

REM ==================================================================
REM  [3/4] Feltoltes a GitHubra (Git Push)
REM ==================================================================
REM Az exe NEM megy a git repoba: az ffmpeg miatt 100 MB feletti, amit a GitHub
REM visszautasitana. Az exe a [4/4] lepesben a Release-hez csatolva kerul fel
REM (ott 2 GB a limit).
echo.
echo [3/4] Feltoltes a GitHubra (Git Push)...
git add -A
git commit -m "Release: Build !NEW_BUILD! (Auto-Build)"
if errorlevel 1 (
    echo.
    echo [!] A commit nem sikerult.
    pause
    exit /b 1
)

git rev-parse --abbrev-ref --symbolic-full-name "@{u}" >nul 2>&1
if errorlevel 1 (
    git push --set-upstream origin "!AG!"
) else (
    git push
)
if %ERRORLEVEL% neq 0 (
    echo.
    echo [!] A git push nem sikerult - a kiadas felbeszakadt.
    echo     Gyakori okok: nincs bejelentkezve a git, vagy a tavoli agban ujabb
    echo     commit van ^(git pull --rebase^). A commit helyben megvan.
    pause
    exit /b 1
)

REM ==================================================================
REM  [4/4] GitHub Release
REM ==================================================================
echo.
echo [4/4] GitHub Release keszitese (build-!NEW_BUILD!)...
REM A gh kimenetet NEM nyeljuk el, es a vegen VISSZAELLENORIZZUK, hogy a kiadas
REM tenyleg letrejott-e - a verdikt a visszaolvasas, nem a returncode.
set "RELEASE_OK=0"
if "!GH_OK!"=="0" (
    echo       [HIBA] A gh nem hasznalhato - a kiadas KIMARAD.
    goto :ellenorzes
)

gh release view "build-!NEW_BUILD!" >nul 2>&1
if not errorlevel 1 (
    echo       A kiadas mar letezik - az exe felulirasa...
    gh release upload "build-!NEW_BUILD!" "dist/%EXE_NAME%" --clobber
) else (
    gh release create "build-!NEW_BUILD!" "dist/%EXE_NAME%" --title "%APP_CIM% - build !NEW_BUILD!" --notes "Automatikus kiadas. Build !NEW_BUILD!."
)

:ellenorzes
if "!GH_OK!"=="1" (
    gh release view "build-!NEW_BUILD!" >nul 2>&1
    if not errorlevel 1 set "RELEASE_OK=1"
)

echo.
if "!RELEASE_OK!"=="1" (
    echo ==========================================
    echo    SIKERES KIADAS: Build !NEW_BUILD!
    echo    Kiadas: build-!NEW_BUILD!  ^(exe: !MERET_MB! MB^)
    echo ==========================================
) else (
    echo ==========================================
    echo    Build !NEW_BUILD! PUSHOLVA, DE A KIADAS KIMARADT!
    echo ==========================================
    echo   A kod fent van a GitHubon, de a "build-!NEW_BUILD!" kiadas az exe-vel
    echo   nem jott letre. Potlas kezzel:
    echo     gh release create "build-!NEW_BUILD!" "dist/%EXE_NAME%" --title "%APP_CIM% - build !NEW_BUILD!" --notes "Automatikus kiadas."
    echo.
    echo   A fenti gh-kimenetben ott a pontos hibauzenet - olvasd el.
)
echo.
pause
exit /b 0

:pip_hiba
echo.
echo   [HIBA] A csomagok telepitese nem sikerult.
echo          Probald kezzel:  %PY% -m pip install customtkinter pyinstaller
echo.
pause
exit /b 1
