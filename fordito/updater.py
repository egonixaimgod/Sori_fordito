"""Önfrissítés: új build keresése a GitHubon, letöltés és az exe lecserélése.

A kiadásokat a rebuild_es_github_push.bat készíti: a címke `build-N`, az exe
pedig mellékletként van feltöltve. Ugyanaz a megoldás, mint a YouTube Letöltőben
és a DriverVarázslóban.
"""

from __future__ import annotations

import json
import locale
import os
import re
import subprocess
import urllib.request

from .logsetup import get_logger
from .util import app_dir

log = get_logger("frissites")

GITHUB_REPO = "egonixaimgod/Sori_fordito"
FRISSITES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
FRISSITES_OLDAL = f"https://github.com/{GITHUB_REPO}/releases/latest"
EXE_NEV = "MagyarFeliratFordito.exe"
USER_AGENT = "MagyarFeliratFordito"


def frissites_mappa() -> str:
    # Az exe mellett van, hogy a csere ugyanazon a meghajtón egyszerű átnevezés legyen.
    return os.path.join(app_dir(), "frissites")


def legujabb_kiadas() -> tuple[int | None, str | None]:
    """A GitHubon közzétett legfrissebb build. -> (build_szam, exe_url).

    Ha a címke nem `build-N` alakú, inkább nem ajánlunk frissítést, mint hogy
    rosszat találjunk ki belőle.
    """
    keres = urllib.request.Request(FRISSITES_API, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(keres, timeout=15) as valasz:
        adat = json.loads(valasz.read().decode("utf-8"))

    talalat = re.fullmatch(r"build-(\d+)", (adat.get("tag_name") or "").strip())
    if not talalat:
        return None, None
    exe_url = None
    for melleklet in adat.get("assets") or []:
        if (melleklet.get("name") or "").lower().endswith(".exe"):
            exe_url = melleklet.get("browser_download_url")
            break
    return int(talalat.group(1)), exe_url


def frissites_letoltes(url: str, cel: str, halad=None) -> str:
    """Az új exe letöltése. Csak akkor ad vissza sikert, ha tényleg program.

    A `halad` visszahívás 0-100 közötti százalékot kap."""
    os.makedirs(os.path.dirname(cel), exist_ok=True)
    tmp = cel + ".tmp"
    keres = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(keres, timeout=60) as valasz:
        teljes = int(valasz.headers.get("Content-Length") or 0)
        kesz = 0
        with open(tmp, "wb") as f:
            while True:
                darab = valasz.read(256 * 1024)
                if not darab:
                    break
                f.write(darab)
                kesz += len(darab)
                if halad and teljes:
                    halad(kesz * 100.0 / teljes)

    # Mielőtt lecserélnénk vele a futó programot, győződjünk meg róla, hogy
    # nem egy hibaoldal vagy csonka fájl érkezett.
    meret = os.path.getsize(tmp)
    with open(tmp, "rb") as f:
        eleje = f.read(2)
    if eleje != b"MZ" or meret < 1024 * 1024 or (teljes and meret != teljes):
        os.remove(tmp)
        raise RuntimeError(f"A letöltött fájl nem futtatható program ({meret} byte).")
    os.replace(tmp, cel)
    return cel


def tiszta_kornyezet() -> dict:
    """Környezet a PyInstaller saját jelölései nélkül.

    A befagyasztott program környezetében ott van a `_PYI_APPLICATION_HOME_DIR`
    (a kicsomagolt `_MEI...` mappa) és a `_PYI_PARENT_PROCESS_LEVEL`. Ezeket a
    segédszkript, és rajta keresztül az általa indított ÚJ exe is örökölné - a
    bootloader ilyenkor azt hiszi, hogy ő egy már kicsomagolt gyerekfolyamat,
    és a régi (időközben törölt) mappából próbálja betölteni a python DLL-t:
    "Failed to load Python DLL". Ezért az újraindításnál ezeket ki kell szedni.
    """
    return {k: v for k, v in os.environ.items()
            if not k.startswith("_PYI") and k != "_MEIPASS2"}


def frissites_telepites(uj_exe: str, cel_exe: str, pid: int | None = None) -> None:
    """A futó exe lecserélése és a program újraindítása.

    Windows alatt a futó exét nem lehet felülírni, ezért egy apró segéd-
    parancsfájl végzi: megvárja, míg ez a folyamat kilép, átmozgatja az új
    fájlt a régi helyére, elindítja, majd törli önmagát.
    """
    mappa = frissites_mappa()
    os.makedirs(mappa, exist_ok=True)
    bat = os.path.join(mappa, "frissit.bat")
    # A PID-re várni NEM elég: a onefile exe két folyamat (a kicsomagoló
    # bootloader és a Python gyerek), és az os.getpid() a gyereké. A szülő még
    # fogja az exe-t, amikor a gyerek már kilépett - ha ilyenkor cseréljük le,
    # a következő indulás "Failed to load Python DLL" hibával jár. Ezért a
    # valódi feltétel az, hogy a fájl cserélhető-e: addig próbáljuk, amíg sikerül.
    tartalom = (
        "@echo off\r\n"
        "setlocal enabledelayedexpansion\r\n"
        'set "PID=%~1"\r\n'
        "set /a n=0\r\n"
        ":varakozas\r\n"
        'tasklist /fi "pid eq %PID%" /nh 2>nul | find /i "%PID%" >nul\r\n'
        "if errorlevel 1 goto csere\r\n"
        "ping -n 2 127.0.0.1 >nul\r\n"
        "set /a n+=1\r\n"
        "if !n! lss 30 goto varakozas\r\n"
        ":csere\r\n"
        "set /a n=0\r\n"
        ":csere_ismet\r\n"
        f'move /y "{uj_exe}" "{cel_exe}" >nul 2>&1\r\n'
        "if not errorlevel 1 goto inditas\r\n"
        "ping -n 2 127.0.0.1 >nul\r\n"
        "set /a n+=1\r\n"
        "if !n! lss 30 goto csere_ismet\r\n"
        # Ha egy percig sem engedte el senki, inkább nem indítunk el egy
        # félig lecserélt programot - a letöltött exe a helyén marad.
        "exit /b 1\r\n"
        ":inditas\r\n"
        "ping -n 3 127.0.0.1 >nul\r\n"
        f'start "" "{cel_exe}"\r\n'
        'del "%~f0"\r\n'
    )
    # A cmd.exe a rendszer kódlapján olvassa a parancsfájlt, nem utf-8-ban.
    # A newline="" nélkülözhetetlen: a szövegmód különben a saját \r\n-jeinket
    # \r\r\n-re fordítaná, amitől a cmd nem találja meg a `goto` címkéit.
    with open(bat, "w", encoding=locale.getpreferredencoding(False),
              errors="replace", newline="") as f:
        f.write(tartalom)

    log.info("Frissítés telepítése: %s -> %s", uj_exe, cel_exe)
    # Csak CREATE_NO_WINDOW: a DETACHED_PROCESS-szel együtt a szkript konzol
    # nélkül maradna, és a tasklist/find/ping hívásai megbízhatatlanná
    # válnának. A gyerekfolyamat így is túléli a kilépésünket.
    subprocess.Popen(["cmd", "/c", bat, str(pid or os.getpid())],
                     creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True,
                     env=tiszta_kornyezet())
