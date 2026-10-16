# -*- coding: utf-8 -*-
"""Y-OGG auto-update: app releases from GitHub + yt-dlp self-update.

Author: de1ze1 (Вадим Угаров). Free to use.

App update flow (frozen build only):
  1. GET https://api.github.com/repos/SterbenAlex/Y-OGG/releases/latest
  2. compare tag (``1.3.0`` / ``v1.2.0``) with APP_VERSION
  3. download the first ``.zip`` asset (``Y-OGG.zip``) to %TEMP%, extract
  4. write a small ``yogg_updater.cmd`` that waits for our PID to exit,
     robocopies the new files over the install folder WITHOUT deleting
     anything and skipping cookies.txt / Downloads / Finished / settings,
     then relaunches Y-OGG.exe.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from y_ogg import APP_NAME, APP_VERSION, _hidden_kwargs, _YTDLP_OK, yogg_root

REPO = "SterbenAlex/Y-OGG"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
SETTINGS_NAME = "yogg_settings.json"
# Never overwritten / deleted by the updater.
PRESERVE_FILES = ("cookies.txt", SETTINGS_NAME)
PRESERVE_DIRS = ("Downloads", "Finished")
YTDLP_MAX_AGE_DAYS = 7

DEFAULT_SETTINGS = {
    "check_updates_on_start": True,
    "auto_update_ytdlp": True,
    "ytdlp_last_update": 0.0,
}


def current_version() -> str:
    """APP_VERSION, overridable for tests via env Y_OGG_FAKE_VERSION."""
    return os.environ.get("Y_OGG_FAKE_VERSION") or APP_VERSION


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


# ---------------------------------------------------------------- settings

def settings_path() -> Path:
    return yogg_root() / SETTINGS_NAME


def load_settings() -> dict:
    data = dict(DEFAULT_SETTINGS)
    try:
        raw = json.loads(settings_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data.update(raw)
    except Exception:
        pass
    return data


def save_settings(data: dict) -> None:
    try:
        settings_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------- versions

def parse_version(tag: str) -> tuple[int, ...]:
    """'v1.2.0' / '1.3.0' / 'Y-OGG 1.4' -> (1, 2, 0). Unknown -> ()."""
    m = re.search(r"(\d+(?:\.\d+)*)", tag or "")
    if not m:
        return ()
    parts = [int(x) for x in m.group(1).split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(remote: str, local: str) -> bool:
    r, l = parse_version(remote), parse_version(local)
    return bool(r) and bool(l) and r > l


@dataclass
class ReleaseInfo:
    tag: str
    version: str
    name: str
    url: str
    zip_url: str
    zip_name: str
    zip_size: int
    notes: str


def _ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


def _get(url: str, timeout: float, accept: str = "application/vnd.github+json") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}", "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as resp:
        return resp.read()


def fetch_latest_release(timeout: float = 6.0) -> ReleaseInfo:
    data = json.loads(_get(API_LATEST, timeout).decode("utf-8"))
    zip_url, zip_name, zip_size = "", "", 0
    assets = data.get("assets") or []
    # Prefer Y-OGG.zip, else any .zip asset.
    assets = sorted(assets, key=lambda a: 0 if a.get("name") == "Y-OGG.zip" else 1)
    for a in assets:
        if str(a.get("name", "")).lower().endswith(".zip"):
            zip_url = a.get("browser_download_url", "")
            zip_name = a.get("name", "")
            zip_size = int(a.get("size") or 0)
            break
    tag = data.get("tag_name", "")
    v = ".".join(str(x) for x in parse_version(tag)) or tag
    return ReleaseInfo(tag, v, data.get("name", ""), data.get("html_url", ""), zip_url, zip_name, zip_size, data.get("body", "") or "")


def check_for_update(timeout: float = 6.0) -> ReleaseInfo | None:
    """Newer release or None. Raises on network errors (caller decides)."""
    rel = fetch_latest_release(timeout)
    if is_newer(rel.tag, current_version()) and rel.zip_url:
        return rel
    return None


# ---------------------------------------------------------------- app apply

def download_release_zip(rel: ReleaseInfo, progress=None, timeout: float = 30.0) -> Path:
    work = Path(tempfile.mkdtemp(prefix="yogg_upd_"))
    dst = work / (rel.zip_name or "Y-OGG.zip")
    req = urllib.request.Request(rel.zip_url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as resp, open(dst, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or rel.zip_size or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done / total)
    return dst


def extract_release(zip_path: Path) -> Path:
    """Extract and return the folder that contains Y-OGG.exe."""
    out = zip_path.parent / "extracted"
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if not info.flag_bits & 0x800:
                # tar.exe -a writes OEM (cp866) names without the UTF-8 flag.
                raw = info.filename.encode("cp437", errors="replace")
                for enc in ("utf-8", "cp866"):
                    try:
                        info.filename = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
            zf.extract(info, out)
    for exe in sorted(out.rglob("Y-OGG.exe"), key=lambda p: len(p.parts)):
        return exe.parent
    raise RuntimeError("В архиве обновления не найден Y-OGG.exe")


def write_updater_script(src: Path, dst: Path, pid: int, exe_name: str = "Y-OGG.exe") -> Path:
    """Generate a .cmd that waits for *pid*, copies src -> dst, relaunches.

    robocopy without /MIR or /PURGE never deletes anything in dst.
    """
    script = Path(tempfile.gettempdir()) / f"yogg_updater_{pid}.cmd"
    log = Path(tempfile.gettempdir()) / "yogg_updater.log"
    xf = " ".join(f'"{n}"' for n in PRESERVE_FILES)
    xd = " ".join(f'"{n}"' for n in PRESERVE_DIRS)
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "setlocal",
        f'set "SRC={src}"',
        f'set "DST={dst}"',
        f'set "LOG={log}"',
        f"echo [%date% %time%] waiting for PID {pid} > \"%LOG%\"",
        "set /a N=0",
        ":wait",
        f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul',
        "if errorlevel 1 goto copy",
        "set /a N+=1",
        "if %N% GEQ 120 goto copy",
        "ping -n 2 127.0.0.1 >nul",
        "goto wait",
        ":copy",
        "ping -n 2 127.0.0.1 >nul",
        f'robocopy "%SRC%" "%DST%" /E /R:5 /W:1 /NP /NFL /NDL /XF {xf} /XD {xd} >> "%LOG%"',
        'echo robocopy exit %errorlevel% >> "%LOG%"',
        'if not exist "%DST%\\Downloads" mkdir "%DST%\\Downloads"',
        'if not exist "%DST%\\Finished" mkdir "%DST%\\Finished"',
        "if not \"%YOGG_NO_RELAUNCH%\"==\"\" goto end",
        f'echo relaunch "%DST%\\{exe_name}" >> "%LOG%"',
        f'start "" "%DST%\\{exe_name}"',
        ":end",
        'echo done >> "%LOG%"',
        "endlocal",
        '(goto) 2>nul & del "%~f0"',
    ]
    script.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return script


def launch_updater(script: Path, extra_env: dict | None = None) -> subprocess.Popen:
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    kw = _hidden_kwargs()
    kw["stdout"] = subprocess.DEVNULL
    kw["stderr"] = subprocess.DEVNULL
    if sys.platform == "win32":
        kw["creationflags"] = kw.get("creationflags", 0) | 0x00000200  # NO_WINDOW | NEW_PROCESS_GROUP
    return subprocess.Popen(["cmd.exe", "/c", str(script)], env=env, close_fds=True, **kw)


def prepare_app_update(rel: ReleaseInfo, progress=None) -> Path:
    """Download + extract + write script. Returns script path (not started)."""
    zip_path = download_release_zip(rel, progress=progress)
    src = extract_release(zip_path)
    return write_updater_script(src, yogg_root(), os.getpid())


# ---------------------------------------------------------------- yt-dlp

EXTRACTOR_ERR = re.compile(
    r"(ExtractorError|Unable to extract|Unsupported URL|nsig|signature|"
    r"Requested format is not available|Sign in to confirm|HTTP Error 403|"
    r"please report this issue|yt-dlp -U|Confirm you are on the latest version)",
    re.IGNORECASE,
)


def looks_like_extractor_error(text: str) -> bool:
    return bool(EXTRACTOR_ERR.search(text or ""))


def ytdlp_version(path: Path) -> str:
    try:
        p = subprocess.run([str(path), "--version"], timeout=20, **_hidden_kwargs())
        return (p.stdout or b"").decode("utf-8", "replace").strip()
    except Exception:
        return ""


def ytdlp_age_days(path: Path) -> float:
    """Age by version string (YYYY.MM.DD), fallback file mtime."""
    v = ytdlp_version(path)
    m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", v)
    try:
        if m:
            t = time.mktime((int(m.group(1)), int(m.group(2)), int(m.group(3)), 0, 0, 0, 0, 0, -1))
        else:
            t = path.stat().st_mtime
        return max(0.0, (time.time() - t) / 86400.0)
    except Exception:
        return 0.0


def update_ytdlp(path: Path, timeout: float = 180.0) -> tuple[bool, str, str, str]:
    """Run ``yt-dlp -U`` hidden. Returns (ok, old_version, new_version, log)."""
    old = ytdlp_version(path)
    try:
        p = subprocess.run([str(path), "-U"], timeout=timeout, **_hidden_kwargs())
        log = ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", "replace").strip()
        ok = p.returncode == 0
    except Exception as exc:
        log, ok = str(exc), False
    _YTDLP_OK.pop(str(path).lower(), None)
    new = ytdlp_version(path) or old
    return ok, old, new, log


def ytdlp_needs_update(path: Path, settings: dict) -> bool:
    last = float(settings.get("ytdlp_last_update") or 0)
    if time.time() - last < YTDLP_MAX_AGE_DAYS * 86400:
        return False
    return ytdlp_age_days(path) > YTDLP_MAX_AGE_DAYS
