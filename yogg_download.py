# -*- coding: utf-8 -*-
"""Download audio as MP3 via yt-dlp (YouTube, SoundCloud, VK, and others).

Author: de1ze1 (Вадим Угаров). Free to use.
Does not scrape browser cookies. Optional Netscape cookies.txt next to the app.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from y_ogg import (
    YoggError,
    find_exe,
    require_tools,
    safe_stem,
    tail,
    yogg_root,
)

PLAYER_CLIENTS = ("web", "ios", "android")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PCT_RE = re.compile(r"(\d+(?:\.\d+)?)%")

HOST_LABELS = {
    "youtube": "YouTube",
    "soundcloud": "SoundCloud",
    "vk": "VK (ВКонтакте)",
    "other": "другой сайт (попробуем yt-dlp)",
}

SUPPORTED_HINT = "YouTube, SoundCloud, VK"

VK_AUTH_MARKERS = (
    "sign in",
    "login required",
    "please log in",
    "authorization",
    "auth required",
    "cookies",
    "cookie",
    "captcha",
    "access denied",
    "private video",
    "this video is only available",
    "unable to download webpage",
    "http error 401",
    "http error 403",
    "status code 401",
    "status code 403",
    "vk.com/login",
    "нужно авторизоваться",
    "войдите",
)


def cookies_path() -> Path | None:
    p = yogg_root() / "cookies.txt"
    return p if p.is_file() else None


def default_downloads_dir() -> Path:
    """MP3 folder: Downloads next to the app, else ~/Downloads/Y-OGG."""
    primary = yogg_root() / "Downloads"
    try:
        primary.mkdir(parents=True, exist_ok=True)
        return primary
    except OSError:
        pass
    fallback = Path.home() / "Downloads" / "Y-OGG"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text or "")


def normalize_url(raw: str) -> str:
    s = (raw or "").strip().strip('"').strip("'")
    if not s:
        return ""
    if s.startswith("www."):
        s = "https://" + s
    return s


def detect_host(url: str) -> str:
    try:
        parsed = urlparse(url.strip())
        host = (parsed.netloc or "").lower()
    except Exception:
        host = url.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]
    if (
        host in ("youtu.be", "youtube.com", "music.youtube.com", "youtube-nocookie.com")
        or host.endswith(".youtube.com")
        or host.endswith(".youtu.be")
    ):
        return "youtube"
    if host in ("soundcloud.com", "on.soundcloud.com", "m.soundcloud.com") or host.endswith(
        ".soundcloud.com"
    ):
        return "soundcloud"
    if (
        host in ("vk.com", "vk.ru", "vkvideo.ru", "vkvideo.com", "m.vk.com", "m.vk.ru")
        or host.endswith(".vk.com")
        or host.endswith(".vk.ru")
        or host.endswith(".vkvideo.ru")
    ):
        return "vk"
    return "other"


def host_label(url: str) -> str:
    return HOST_LABELS.get(detect_host(url), HOST_LABELS["other"])


def looks_like_url(raw: str) -> bool:
    s = normalize_url(raw)
    if not s:
        return False
    low = s.lower()
    if low.startswith(("http://", "https://")):
        return True
    return bool(re.match(r"^[a-z0-9.-]+\.[a-z]{2,}(/|$)", low))


def is_playlist_url(url: str) -> bool:
    """True when the URL points at a playlist/album, not a single video+list combo."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    qs = parse_qs(parsed.query or "")
    kind = detect_host(url)

    if kind == "youtube":
        if path.rstrip("/").endswith("/playlist") or "/playlist" in path:
            return True
        if "list" in qs and not qs.get("v"):
            # youtu.be/VIDEO?list=… still has a video id in the path
            if "youtu.be" in host and path.strip("/"):
                return False
            return True
        return False

    if kind == "soundcloud":
        return "/sets/" in path

    if kind == "vk":
        if "playlist" in path or "/audios" in path or "/music/" in path:
            return True
        if "playlist" in (parsed.query or "").lower():
            return True
        return False

    if "/playlist" in path or "/sets/" in path or "/album/" in path:
        return True
    return False


def find_ytdlp() -> Path | None:
    return find_exe("yt-dlp")


def require_ytdlp() -> Path:
    p = find_ytdlp()
    if p is None:
        from y_ogg import _bin_dirs

        roots = "\n".join(f"  • {c}" for c in _bin_dirs())
        raise YoggError(
            "Не найден yt-dlp.exe.",
            "Скачивание по ссылке нужно yt-dlp.\n\nИскал в:\n"
            + roots
            + "\n\nПоложи yt-dlp.exe в bin рядом с программой "
            r"(например C:\Users\de1ze1\Desktop\Y-OGG\bin\yt-dlp.exe).",
        )
    return p


def _cookies_args() -> list[str]:
    ck = cookies_path()
    if ck:
        return ["--cookies", str(ck)]
    return []


def _vk_cookies_hint() -> str:
    dest = yogg_root() / "cookies.txt"
    return (
        "VK (ВКонтакте) часто требует авторизацию.\n"
        "Положи cookies.txt (формат Netscape) рядом с программой:\n"
        f"  {dest}\n\n"
        "Браузерные cookies программа сама не забирает — "
        "экспортируй файл вручную (расширение Get cookies.txt LOCALLY и т.п.)."
    )


def _looks_like_vk_auth(log: str) -> bool:
    low = (log or "").lower()
    return any(m in low for m in VK_AUTH_MARKERS)


def _looks_like_no_js_runtime(log: str) -> bool:
    low = (log or "").lower()
    return (
        "js runtimes: none" in low
        or re.search(r"js runtime[s]?:\s*none", low) is not None
        or ("deno" in low and ("not found" in low or "unavailable" in low))
    )


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suf = path.stem, path.suffix
    for i in range(2, 1000):
        cand = path.with_name(f"{stem} ({i}){suf}")
        if not cand.exists():
            return cand
    return path.with_name(f"{stem}_{os.getpid()}{suf}")


def _ffmpeg_location() -> str | None:
    ffmpeg = find_exe("ffmpeg")
    if ffmpeg is None:
        return None
    return str(ffmpeg.parent)


@dataclass
class DownloadResult:
    path: Path
    title: str
    host: str
    used_cookies: bool


def _base_cmd(
    ytdlp: Path,
    dest_dir: Path,
    *,
    playlist_mode: str,
) -> list[str]:
    """playlist_mode: 'single' | 'first' | 'all'."""
    out_tmpl = str(dest_dir / "%(title).180B [%(id)s].%(ext)s")
    cmd = [
        str(ytdlp),
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--windows-filenames",
        "--no-cache-dir",
        "--force-overwrites",
        "--no-mtime",
        "--no-update",
        "--ignore-config",
        "--no-check-certificate",
        "--legacy-server-connect",
        "--socket-timeout", "30",
        "--newline",
        "--color", "never",
        "-o", out_tmpl,
        "--print", "after_move:%(title)s",
        "--print", "after_move:%(filepath)s",
    ]
    if playlist_mode == "all":
        cmd.append("--yes-playlist")
    elif playlist_mode == "first":
        cmd += ["--yes-playlist", "--playlist-items", "1"]
    else:
        cmd.append("--no-playlist")

    ffdir = _ffmpeg_location()
    if ffdir:
        cmd += ["--ffmpeg-location", ffdir]
    cmd += _cookies_args()
    return cmd


def _error_text(host: str, log: str) -> str:
    clean = strip_ansi(log)
    parts = [tail(clean, 2200) or "(yt-dlp ничего не вывел)"]
    if host == "vk" or _looks_like_vk_auth(clean):
        if host == "vk":
            parts.append(_vk_cookies_hint())
    if host == "youtube":
        if _looks_like_no_js_runtime(clean):
            parts.append(
                "В логе yt-dlp: JS runtimes: none.\n"
                "YouTube иногда требует Deno (https://deno.com). "
                "Либо положи cookies.txt (Netscape) рядом с программой."
            )
        elif "403" in clean:
            parts.append(
                "HTTP 403 на YouTube. Положи cookies.txt (формат Netscape) "
                f"рядом с программой:\n  {yogg_root() / 'cookies.txt'}\n"
                "Или обнови yt-dlp.exe."
            )
        elif "eof occurred" in clean.lower() or "sslerror" in clean.lower():
            parts.append(
                "Сбой TLS/SSL при обращении к YouTube. "
                "Проверь VPN, прокси и антивирус (HTTPS scanning). "
                "Можно положить cookies.txt (Netscape) рядом с программой."
            )
    if host == "other":
        parts.append(
            f"Поддерживаются напрямую: {SUPPORTED_HINT}. "
            "Для других сайтов всё равно вызывается yt-dlp."
        )
    ck = cookies_path()
    if ck:
        parts.append(f"Использован файл cookies: {ck}")
    return "\n\n".join(parts)


def _parse_after_move(stdout: str, dest_dir: Path) -> tuple[Path | None, str]:
    lines = [ln.strip() for ln in (stdout or "").splitlines() if ln.strip()]
    title = ""
    filepath: Path | None = None
    # Prefer the last pair of after_move prints (title then path).
    paths = [ln for ln in lines if ln.lower().endswith((".mp3", ".m4a", ".opus", ".webm", ".ogg"))]
    if paths:
        filepath = Path(paths[-1])
    titles = [
        ln for ln in lines
        if not ln.lower().endswith((".mp3", ".m4a", ".opus", ".webm", ".ogg"))
        and not ln.startswith("[")
        and "http" not in ln.lower()
        and len(ln) < 400
    ]
    if titles:
        title = titles[-1]
    if filepath is None or not filepath.is_file():
        files = [
            p for p in dest_dir.iterdir()
            if p.is_file() and p.suffix.lower() in {".mp3", ".m4a", ".opus", ".webm", ".ogg"}
        ]
        if files:
            filepath = max(files, key=lambda p: p.stat().st_mtime)
    return filepath, title


def _progress_from_line(line: str) -> float | None:
    if "[download]" not in line.lower() and "extract" not in line.lower():
        # still try percent anywhere
        pass
    if "eta" not in line.lower() and "[download]" not in line.lower():
        return None
    m = PCT_RE.search(line)
    if not m:
        return None
    try:
        return max(0.0, min(1.0, float(m.group(1)) / 100.0))
    except ValueError:
        return None


def _popen(cmd: list[str]) -> subprocess.Popen[str]:
    kw: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if sys.platform == "win32":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        kw["startupinfo"] = si
    return subprocess.Popen(cmd, **kw)


def _run_ytdlp(
    cmd: list[str],
    status: Callable[[str], None],
    progress: Callable[[float], None] | None,
) -> tuple[int, str]:
    proc = _popen(cmd)
    chunks: list[str] = []
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            line = strip_ansi(raw).rstrip()
            if not line:
                continue
            chunks.append(line)
            pct = _progress_from_line(line)
            if pct is not None and progress is not None:
                progress(pct)
            low = line.lower()
            if "[download]" in low or "[extractaudio]" in low or "destination" in low:
                # Keep the UI snappy without dumping every percent line.
                if "eta" in low:
                    status(f"Скачиваю… {line.split('[download]')[-1].strip()[:80]}")
                elif "extractaudio" in low or "destination" in low:
                    status("Извлекаю MP3 (ffmpeg)…")
        rc = proc.wait()
    except Exception:
        try:
            proc.kill()
        except OSError:
            pass
        raise
    log = "\n".join(chunks[-400:])
    return rc, log


def download_audio_mp3(
    url: str,
    dest_dir: Path,
    *,
    playlist_mode: str = "single",
    status: Callable[[str], None] | None = None,
    progress: Callable[[float], None] | None = None,
) -> DownloadResult:
    """Download one URL as MP3 into dest_dir.

    playlist_mode:
      single — --no-playlist (video+list → one video)
      first  — playlist item 1 only
      all    — whole playlist (user confirmed)
    """
    url = normalize_url(url)
    if not looks_like_url(url):
        raise YoggError("Вставь ссылку на трек (http/https).")
    dest_dir = dest_dir.expanduser().resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    log_fn = status or (lambda _m: None)
    host = detect_host(url)
    ytdlp = require_ytdlp()
    # ffmpeg is required to extract MP3
    require_tools()

    extras: list[tuple[str, list[str]]] = [("", [])]
    if host == "youtube":
        extras = [
            (c, ["--force-ipv4", "--extractor-args", f"youtube:player_client={c}"])
            for c in PLAYER_CLIENTS
        ]
        # SSL EOF on some networks with IPv4-only; last try without --force-ipv4
        extras.append(("web/auto", ["--extractor-args", "youtube:player_client=web"]))

    last_log = ""
    for i, (label, extra) in enumerate(extras):
        if host == "youtube":
            log_fn(f"YouTube: скачиваю аудио (player_client={label})…")
        elif host == "soundcloud":
            log_fn("SoundCloud: скачиваю аудио…")
        elif host == "vk":
            log_fn("VK: скачиваю аудио…")
        else:
            log_fn(f"Скачиваю через yt-dlp ({host_label(url)})…")

        cmd = _base_cmd(ytdlp, dest_dir, playlist_mode=playlist_mode)
        cmd += extra
        cmd.append(url)
        rc, log = _run_ytdlp(cmd, log_fn, progress)
        last_log = log
        if rc == 0:
            path, title = _parse_after_move(log, dest_dir)
            if path is None or not path.is_file():
                raise YoggError(
                    "yt-dlp завершился без файла.",
                    _error_text(host, last_log),
                )
            # Prefer a real .mp3; if extract failed, keep whatever we got.
            if path.suffix.lower() != ".mp3":
                mp3s = [p for p in dest_dir.glob("*.mp3") if p.stat().st_mtime >= path.stat().st_mtime - 2]
                if mp3s:
                    path = max(mp3s, key=lambda p: p.stat().st_mtime)
            final = path
            # Sanitize leftover odd names.
            wanted = dest_dir / f"{safe_stem(title or path.stem)}.mp3"
            if final.suffix.lower() == ".mp3" and final.name != wanted.name:
                try:
                    target = unique_path(wanted) if wanted.exists() and wanted != final else wanted
                    if target != final:
                        final.replace(target)
                        final = target
                except OSError:
                    pass
            return DownloadResult(
                path=final,
                title=title or final.stem,
                host=host,
                used_cookies=cookies_path() is not None,
            )
        if host == "youtube" and i < len(extras) - 1:
            log_fn(f"YouTube: {label} не сработал, пробую другой клиент…")
            continue
        break

    if host == "vk":
        raise YoggError(
            "Не удалось скачать аудио с VK.",
            _error_text("vk", last_log),
        )
    if host == "youtube":
        raise YoggError(
            "Не удалось скачать аудио с YouTube.",
            _error_text("youtube", last_log),
        )
    raise YoggError(
        f"Не удалось скачать аудио ({host_label(url)}).",
        _error_text(host, last_log),
    )


def playlist_choice_needed(url: str) -> bool:
    return is_playlist_url(normalize_url(url))
