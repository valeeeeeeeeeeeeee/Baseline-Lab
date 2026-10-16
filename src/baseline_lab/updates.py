"""Update check: asks GitHub for the latest release and compares it with this version; and the
update itself: the release's zip is downloaded and extracted next to the app, and a small
script swaps the folders once the app has closed.

Only the standard library is used. Any failure of the check (no network, GitHub out of reach,
an answer in another shape) just means there is no notice. A file downloaded by the app itself
gets no mark of having come from the internet, so SmartScreen is only met on the first install.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, NamedTuple

from . import __version__

REPO = "valeeeeeeeeeeeeee/Baseline-Lab"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"
DOWNLOADS = f"https://github.com/{REPO}/releases/download/"
STAGE = ".baseline-lab-update-"  # a download in progress, beside the app's folder


class Release(NamedTuple):
    version: str
    page: str                  # the release's page, with its notes (the changelog)
    zip: str | None = None     # the app folder, zipped; None if the release has none
    digest: str | None = None  # "sha256:..." of the zip, when GitHub gives it


def parse(version: str) -> tuple[int, ...] | None:
    """"v1.2.3" or "1.2.3" as (1, 2, 3); None if it is not a release number (the source runs as
    "dev": the release workflow writes the tag into `__version__`)."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", version.strip())
    return tuple(int(n) for n in m.group(1).split(".")) if m else None


def newer(latest: str, current: str = __version__) -> bool:
    a, b = parse(latest), parse(current)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))  # "1.1" and "1.1.0" are the same release
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                "User-Agent": "Baseline-Lab"})


def check(current: str = __version__, timeout: float = 5.0) -> Release | None:
    """The latest release if it is newer than `current`, else None. It waits for the network:
    call it off the interface's thread."""
    if parse(current) is None:
        return None
    try:
        with urllib.request.urlopen(_request(API), timeout=timeout) as resp:
            data = json.load(resp)
        tag, url = data["tag_name"], data.get("html_url")
        if not newer(tag, current):
            return None
        assets = data.get("assets") or []
    except Exception:  # noqa: BLE001
        return None
    # the address is handed to the browser: only a page of the repository itself
    if not (isinstance(url, str) and url.startswith(f"https://github.com/{REPO}/")):
        url = PAGE
    zip_url = digest = None
    for asset in assets if isinstance(assets, list) else []:
        link = asset.get("browser_download_url") if isinstance(asset, dict) else None
        if isinstance(link, str) and link.startswith(DOWNLOADS) and link.endswith(".zip"):
            zip_url, digest = link, asset.get("digest")
            break
    if not (isinstance(digest, str) and digest.startswith("sha256:")):
        digest = None
    return Release(tag.strip().lstrip("v"), url, zip_url, digest)


# ------------------------------------------------------------------ installing
def can_install(release: Release | None) -> bool:
    """Only the released exe updates itself (from the source there is no folder to swap)."""
    return (release is not None and release.zip is not None and sys.platform == "win32"
            and bool(getattr(sys, "frozen", False)))


def app_folder() -> Path:
    return Path(sys.executable).resolve().parent


def download(release: Release, progress: Callable[[float], None] = lambda _f: None,
             app: Path | None = None, timeout: float = 30.0) -> tuple[Path, Path]:
    """Downloads the release's zip and extracts it into a new folder beside the app's (the
    swap is then a rename: a move across drives would be a copy). Returns (that folder, the
    new app folder inside it). Raises on any failure, a place where writing needs an
    administrator included, and leaves nothing behind. It waits for the network: call it off
    the interface's thread; `progress` is called there too, with the share done."""
    app = app or app_folder()
    stage = Path(tempfile.mkdtemp(prefix=STAGE, dir=app.parent))
    try:
        archive = stage / "release.zip"
        sha = hashlib.sha256()
        with urllib.request.urlopen(_request(release.zip), timeout=timeout) as resp, \
                open(archive, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while chunk := resp.read(1 << 16):
                out.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                if total:
                    progress(min(done / total, 1.0))
        if release.digest and sha.hexdigest() != release.digest.split(":", 1)[1].lower():
            raise ValueError("the download does not match the release")
        with zipfile.ZipFile(archive) as z:
            z.extractall(stage / "new")  # it drops ".." and drive letters from the names
        archive.unlink()
        exe = Path(sys.executable).name if getattr(sys, "frozen", False) else "Baseline Lab.exe"
        found = sorted((stage / "new").rglob(exe), key=lambda p: len(p.parts))
        if not found:
            raise FileNotFoundError(exe)
        return stage, found[0].parent
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


# The paths come in the environment (BL_*): this file is read in the console's code page,
# which may not have their characters, and what `!VAR!` gives is not parsed again (& ^ ( )
# in a folder's name). The app's folder can only be renamed once nothing in it is open: that
# is the wait for the app to close. On a failure the old folder is put back and the release
# page is opened.
SCRIPT = r"""@echo off
setlocal EnableDelayedExpansion
set tries=0
:wait
move "!BL_APP!" "!BL_STAGE!\old" >nul 2>&1 && goto moved
set /a tries+=1
if !tries! geq 60 goto failed
ping -n 2 127.0.0.1 >nul
goto wait
:moved
set tries=0
:place
move "!BL_NEW!" "!BL_APP!" >nul 2>&1 && goto done
set /a tries+=1
if !tries! geq 10 goto restore
ping -n 2 127.0.0.1 >nul
goto place
:restore
move "!BL_STAGE!\old" "!BL_APP!" >nul 2>&1
goto failed
:done
if "!BL_RESTART!"=="1" start "" /d "!BL_APP!" "!BL_APP!\!BL_EXE!"
goto clean
:failed
if "!BL_RESTART!"=="1" if exist "!BL_APP!\!BL_EXE!" start "" /d "!BL_APP!" "!BL_APP!\!BL_EXE!"
start "" "!BL_PAGE!"
:clean
rd /s /q "!BL_STAGE!" >nul 2>&1
(goto) 2>nul & del "%~f0"
"""


def swap(stage: Path, new: Path, page: str, restart: bool,
         app: Path | None = None, exe: str | None = None) -> subprocess.Popen:
    """Starts the script that, once this process has closed, puts the folder `new` in the
    place of the app's and, with `restart`, opens the app again. It goes on by itself: call it
    just before closing."""
    app = app or app_folder()
    fd, script = tempfile.mkstemp(prefix="baseline-lab-update-", suffix=".cmd")
    with os.fdopen(fd, "w", newline="\r\n", encoding="ascii") as f:
        f.write(SCRIPT)
    # what the exe's loader left for itself (its folders, the splash screen) must not reach
    # the app opened again: it would take itself for a part of this one
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("_PYI", "_MEI", "TCL_", "TK_"))}
    env.update(PYINSTALLER_RESET_ENVIRONMENT="1", BL_APP=str(app), BL_STAGE=str(stage),
               BL_NEW=str(new), BL_EXE=exe or Path(sys.executable).name, BL_PAGE=page,
               BL_RESTART="1" if restart else "0")
    return subprocess.Popen(["cmd.exe", "/d", "/c", script], env=env,
                            cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW
                            | subprocess.CREATE_NEW_PROCESS_GROUP)


def discard(stage: Path):
    """Drops a download that will not be installed."""
    shutil.rmtree(stage, ignore_errors=True)


def clean():
    """Drops what a download cut short (the app closed meanwhile) left beside the app."""
    if getattr(sys, "frozen", False):
        for stage in app_folder().parent.glob(STAGE + "*"):
            discard(stage)
