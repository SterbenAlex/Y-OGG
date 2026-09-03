# -*- coding: utf-8 -*-
"""Download audio as MP3 (YouTube / SoundCloud via yt-dlp; VK audio via VK API).

Author: de1ze1 (Вадим Угаров). Free to use.
Does not scrape browser cookies. Optional Netscape cookies.txt next to the app.
yt-dlp VK extractors are video-only — /audioOWNER_ID_TRACK_ID is handled here.
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from y_ogg import (
    YoggError,
    find_exe,
    require_tools,
    run_cmd,
    safe_stem,
    tail,
    yogg_root,
)

PLAYER_CLIENTS = ("web", "ios", "android")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PCT_RE = re.compile(r"(\d+(?:\.\d+)?)%")

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)

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

# /audio{owner}_{id}_{hash?} on vk.com / vk.ru (and z=audio… query form)
_VK_AUDIO_PATH_RE = re.compile(
    r"(?:^|/)audio(-?\d+)_(\d+)(?:_([0-9A-Za-z]+))?(?:[/?#]|$)",
    re.I,
)
_VK_AUDIO_Z_RE = re.compile(
    r"(?:^|[?&]z=)audio(-?\d+)_(\d+)(?:(?:%2F|/|_)([0-9A-Za-z]+))?",
    re.I,
)
_M3U8_TO_MP3 = re.compile(r"/[0-9a-f]+(/audios)?/([0-9a-f]+)/index\.m3u8", re.I)
_VK_STR = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN0PQRSTUVWXYZO123456789+/="


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


@dataclass(frozen=True)
class VkAudioRef:
    owner_id: str
    audio_id: str
    access_hash: str

    @property
    def id_plain(self) -> str:
        return f"{self.owner_id}_{self.audio_id}"

    @property
    def id_full(self) -> str:
        if self.access_hash:
            return f"{self.owner_id}_{self.audio_id}_{self.access_hash}"
        return self.id_plain


def parse_vk_audio_url(url: str) -> VkAudioRef | None:
    """Single-track VK audio: /audioOWNER_ID_TRACK_ID[_hash] or ?z=audio…"""
    try:
        parsed = urlparse((url or "").strip())
    except Exception:
        return None
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]
    vkish = (
        host in ("vk.com", "vk.ru", "m.vk.com", "m.vk.ru")
        or host.endswith(".vk.com")
        or host.endswith(".vk.ru")
    )
    if not vkish:
        return None
    path = parsed.path or ""
    m = _VK_AUDIO_PATH_RE.search(path)
    if m:
        return VkAudioRef(m.group(1), m.group(2), m.group(3) or "")
    q = parsed.query or ""
    frag = parsed.fragment or ""
    for blob in (q, urllib.parse.unquote(q), frag, urllib.parse.unquote(frag)):
        m = _VK_AUDIO_Z_RE.search(blob)
        if m:
            return VkAudioRef(m.group(1), m.group(2), m.group(3) or "")
    return None


def is_vk_audio_url(url: str) -> bool:
    return parse_vk_audio_url(normalize_url(url)) is not None


def is_playlist_url(url: str) -> bool:
    """True when the URL points at a playlist/album, not a single video+list combo."""
    if parse_vk_audio_url(url) is not None:
        return False
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
            "Не найден рабочий yt-dlp.exe.",
            "Скачивание по ссылке нужно yt-dlp (проверка: yt-dlp --version).\n\nИскал в:\n"
            + roots
            + "\n\nПоложи рабочий yt-dlp.exe в bin рядом с программой "
            r"(например C:\Users\de1ze1\Desktop\Y-OGG\bin\yt-dlp.exe)."
            "\nСломанный PyInstaller-билд без _internal\\python310.dll пропускается.",
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
        "Ссылки вида vk.com/audio… и vk.ru/audioOWNER_ID_TRACK_ID — это аудио VK.\n"
        "yt-dlp умеет только видео VK; аудио качается через API VK и нужен cookies.txt.\n"
        "Положи cookies.txt (формат Netscape) рядом с программой:\n"
        f"  {dest}\n\n"
        "Браузерные cookies программа сама не забирает — "
        "экспортируй файл вручную (расширение Get cookies.txt LOCALLY и т.п.).\n"
        "Если сессия истекла — выгрузи cookies заново (значения в лог не пишутся)."
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


# ---------------------------------------------------------------------------
# VK audio (not yt-dlp)
# ---------------------------------------------------------------------------

@dataclass
class _NkCookie:
    domain: str
    name: str
    # value is kept only to build the Cookie header; never logged/printed.


def _load_netscape_vk_cookies(path: Path) -> tuple[list[tuple[str, str, str]], dict[str, int]]:
    """Return ([(domain, name, value), ...], stats). Values are never logged."""
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    rows: list[tuple[str, str, str]] = []
    vk_com = 0
    vk_ru = 0
    session_names = 0
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        http_only = False
        if s.startswith("#HttpOnly_"):
            s = s[len("#HttpOnly_"):]
            http_only = True
        elif s.startswith("#"):
            continue
        parts = s.split("\t")
        if len(parts) < 7:
            parts = re.split(r"\s+", s)
        if len(parts) < 7:
            continue
        domain, _flag, _path, _secure, _exp, name, value = parts[:7]
        domain = (domain or "").lstrip(".").lower()
        name = name.strip()
        if not name or ("vk.com" not in domain and "vk.ru" not in domain):
            continue
        if domain.endswith("vk.com"):
            vk_com += 1
        if domain.endswith("vk.ru"):
            vk_ru += 1
        if name.lower() in {"remixsid", "remixnsid", "remixsid6", "remixstlid"}:
            session_names += 1
        rows.append((domain, name, value))
        _ = http_only
    stats = {
        "bytes": len(raw),
        "vk_com": vk_com,
        "vk_ru": vk_ru,
        "session": session_names,
        "total": len(rows),
    }
    return rows, stats


def _cookie_header(rows: list[tuple[str, str, str]], host: str) -> str:
    """Cookie header for host. Prefer host-matching values; include vk.com and vk.ru."""
    host = (host or "").lower()
    picked: dict[str, tuple[int, str]] = {}
    for domain, name, value in rows:
        if "vk.com" not in domain and "vk.ru" not in domain:
            continue
        score = 0
        if host.endswith("vk.com") and domain.endswith("vk.com"):
            score = 2
        elif host.endswith("vk.ru") and domain.endswith("vk.ru"):
            score = 2
        else:
            score = 1
        prev = picked.get(name)
        if prev is None or score > prev[0]:
            picked[name] = (score, value)
    return "; ".join(f"{n}={v}" for n, (_s, v) in picked.items())


def _cookie_stats_text(stats: dict[str, int]) -> str:
    return (
        f"cookies.txt: {stats.get('bytes', 0)} байт, "
        f"{stats.get('total', 0)} cookie для VK "
        f"(vk.com={stats.get('vk_com', 0)}, vk.ru={stats.get('vk_ru', 0)}, "
        f"session={stats.get('session', 0)}). Значения не показываются."
    )


def _json_shape(obj: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "…"
    if isinstance(obj, dict):
        out = {}
        for i, k in enumerate(obj):
            if i >= 24:
                out["…"] = f"+{len(obj) - 24}"
                break
            out[str(k)] = _json_shape(obj[k], depth + 1)
        return out
    if isinstance(obj, list):
        if not obj:
            return "list(len=0)"
        sample = _json_shape(obj[0], depth + 1)
        return {"list_len": len(obj), "item0": sample}
    if isinstance(obj, str):
        return f"str(len={len(obj)})"
    if obj is None or isinstance(obj, (int, float, bool)):
        return type(obj).__name__
    return type(obj).__name__


def _looks_like_audio_item(item: list) -> bool:
    if len(item) < 6:
        return False
    try:
        int(item[0])
        int(item[1])
    except (TypeError, ValueError):
        return False
    if not isinstance(item[2], str):
        return False
    if not isinstance(item[3], str) or not isinstance(item[4], str):
        return False
    return True


def _walk_audio_items(obj: Any, found: list[list]) -> None:
    if isinstance(obj, list):
        if _looks_like_audio_item(obj):
            found.append(obj)
            return
        for x in obj:
            _walk_audio_items(x, found)
    elif isinstance(obj, dict):
        for v in obj.values():
            _walk_audio_items(v, found)
    elif isinstance(obj, str):
        s = obj.strip()
        if len(s) >= 8 and s[0] in "{[":
            try:
                _walk_audio_items(json.loads(s), found)
            except json.JSONDecodeError:
                pass


def _parse_vk_ajax(body: bytes) -> Any:
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    if text.startswith("{") or text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    # classic VK: <!--123<!><!json>{...}
    m = re.search(r"\{.*\}\s*$", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\[.*\]\s*$", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {"_raw_kind": "non-json", "_raw_len": len(text)}


def _vk_o(string: str) -> str:
    result: list[str] = []
    index2 = 0
    i = 0
    for s in string:
        sym_index = _VK_STR.find(s)
        if sym_index == -1:
            continue
        if index2 % 4 != 0:
            index2 += 1
            i = (i << 6) + sym_index
            result.append(chr(0xFF & i >> (-2 * index2 & 6)))
        else:
            i = sym_index
            index2 += 1
    return "".join(result)


def _vk_s_child(t: str, e: int) -> list[int]:
    i = len(t)
    if not i:
        return []
    o: list[int] = []
    for a in range(i - 1, -1, -1):
        e = (i * (a + 1) ^ e + a) % i
        o.append(e)
    return o[::-1]


def _vk_s(t: str, e: int) -> str:
    i = len(t)
    if not i:
        return t
    o = _vk_s_child(t, e)
    chars = list(t)

    def splice(lst: list[str], a: int, b: int, c: str) -> tuple[list[str], list[str]]:
        return lst[:a] + [c] + lst[a + b :], lst[a : a + b]

    for a in range(1, i):
        chars, y = splice(chars, o[i - 1 - a], 1, chars[a])
        chars[a] = y[0]
    return "".join(chars)


def _vk_r(string: str, shift: str) -> str:
    table = _VK_STR + _VK_STR
    n = len(table)
    out: list[str] = []
    try:
        k = int(shift)
    except (TypeError, ValueError):
        return string
    for s in string:
        idx = table.find(s)
        if idx == -1:
            out.append(s)
            continue
        off = idx - k
        if off < 0:
            off += n
        out.append(table[off])
    return "".join(out)


def _decode_vk_audio_url(mask_url: str, user_id: int) -> str:
    if "audio_api_unavailable" not in mask_url or "?extra=" not in mask_url:
        return mask_url
    try:
        extra = mask_url.split("?extra=", 1)[1]
        vals = extra.split("#")
        tstr = _vk_o(vals[0])
        ops = _vk_o(vals[1]).split("\x09")[::-1] if len(vals) > 1 else []
        for op_data in ops:
            parts = op_data.split("\x0b")
            cmd = parts[0]
            arg = parts[1] if len(parts) > 1 else None
            if cmd == "v":
                tstr = tstr[::-1]
            elif cmd == "r" and arg is not None:
                tstr = _vk_r(tstr, arg)
            elif cmd == "x" and arg:
                xv = ord(arg[0])
                tstr = "".join(chr(ord(c) ^ xv) for c in tstr)
            elif cmd == "s" and arg is not None:
                tstr = _vk_s(tstr, int(arg))
            elif cmd == "i" and arg is not None:
                tstr = _vk_s(tstr, int(arg) ^ int(user_id))
            else:
                return mask_url
        if tstr.startswith("http"):
            return tstr
    except Exception:
        return mask_url
    return mask_url


def _maybe_mp3_from_m3u8(url: str) -> str:
    if "m3u8" not in url.lower():
        return url
    alt = _M3U8_TO_MP3.sub(r"\1/\2.mp3", url)
    return alt if alt != url else url


_INSECURE_CTX: ssl.SSLContext | None = None


def _insecure_ctx() -> ssl.SSLContext:
    """Same posture as the yt-dlp path (--no-check-certificate): AV/proxy MITM."""
    global _INSECURE_CTX
    if _INSECURE_CTX is None:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        _INSECURE_CTX = ctx
    return _INSECURE_CTX


def _http_request(
    method: str,
    url: str,
    *,
    data: bytes | None,
    headers: dict[str, str],
    timeout: float = 30.0,
) -> tuple[int, bytes, str]:
    """Return (status, body, final_url). Cookie values must not appear in errors."""
    last: Exception | None = None
    for ctx in (None, _insecure_ctx()):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                body = resp.read()
                return int(resp.status), body, str(resp.geturl())
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read() or b""
            except Exception:
                body = b""
            return int(exc.code), body, url
        except urllib.error.URLError as exc:
            last = exc
            reason = exc.reason
            if isinstance(reason, ssl.SSLError) and ctx is None:
                continue  # local cert store / TLS scanning: retry unverified
            break
        except Exception as exc:  # socket timeouts etc.
            last = exc
            break
    reason_name = "URLError"
    if isinstance(last, urllib.error.URLError) and last.reason is not None:
        reason_name = type(last.reason).__name__
    elif last is not None:
        reason_name = type(last).__name__
    raise YoggError(
        "Нет соединения с VK (проверь VPN / прокси / антивирус).",
        reason_name,
    ) from None


def _extract_vk_user_id(body: bytes) -> int | None:
    text = body.decode("utf-8", errors="replace")
    patterns = (
        r'id:\s*(\d{2,12})',
        r'"id"\s*:\s*(\d{2,12})',
        r'vk\.id\s*=\s*(\d{2,12})',
    )
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            try:
                n = int(m.group(1))
                if n > 0:
                    return n
            except ValueError:
                continue
    return None


def _pick_stream(item: list, user_id: int | None) -> tuple[str, str, str]:
    url = item[2] if isinstance(item[2], str) else ""
    title = html.unescape(item[3] or "").strip()
    artist = html.unescape(item[4] or "").strip()
    if url and "audio_api_unavailable" in url:
        for uid in (user_id, _safe_int(item[1])):
            if not uid:
                continue
            decoded = _decode_vk_audio_url(url, uid)
            if decoded and "audio_api_unavailable" not in decoded:
                url = decoded
                break
    return url, artist, title


def _safe_int(v: Any) -> int | None:
    try:
        n = int(v)
        return n if n else None
    except (TypeError, ValueError):
        return None


def _ffmpeg_to_mp3(
    stream_url: str,
    dest: Path,
    ffmpeg: Path,
    *,
    extra_headers: dict[str, str] | None,
    status: Callable[[str], None],
    progress: Callable[[float], None] | None,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    attempts: list[list[str]] = []
    header_blob = ""
    if extra_headers:
        # Do not put Cookie in argv unless required; CDNs are usually signed.
        safe = {k: v for k, v in extra_headers.items() if k.lower() != "cookie"}
        if safe:
            header_blob = "".join(f"{k}: {v}\r\n" for k, v in safe.items())

    def base(codec: list[str]) -> list[str]:
        cmd = [
            str(ffmpeg),
            "-hide_banner",
            "-nostdin",
            "-y",
            "-user_agent", CHROME_UA,
        ]
        if header_blob:
            cmd += ["-headers", header_blob]
        cmd += ["-i", stream_url, "-vn", *codec, str(dest)]
        return cmd

    if "m3u8" in stream_url.lower():
        attempts.append(base(["-c:a", "libmp3lame", "-q:a", "0"]))
    else:
        attempts.append(base(["-c:a", "copy"]))
        attempts.append(base(["-c:a", "libmp3lame", "-q:a", "0"]))

    last_err = ""
    for i, cmd in enumerate(attempts):
        status("VK: пишу MP3 через ffmpeg…")
        if progress:
            progress(0.55 + 0.15 * i)
        proc = run_cmd(cmd, timeout=180)
        if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 1000:
            return
        err = (proc.stderr or b"").decode("utf-8", errors="replace")
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        last_err = tail((err + "\n" + out), 800)
        # Strip accidental cookie-like tokens from ffmpeg chatter
        last_err = re.sub(r"(?i)(cookie\s*[:=]\s*)\S+", r"\1[скрыто]", last_err)
    raise YoggError(
        "ffmpeg не смог сохранить MP3 с VK.",
        last_err or "пустой вывод ffmpeg",
    )


def _download_vk_audio(
    url: str,
    dest_dir: Path,
    *,
    status: Callable[[str], None],
    progress: Callable[[float], None] | None,
) -> DownloadResult:
    ref = parse_vk_audio_url(url)
    if ref is None:
        raise YoggError("Это не ссылка на один трек VK (/audioOWNER_ID_TRACK_ID).")

    ck = cookies_path()
    if ck is None:
        raise YoggError(
            "Нет cookies.txt — для аудио VK нужна авторизация.",
            _vk_cookies_hint(),
        )

    try:
        rows, stats = _load_netscape_vk_cookies(ck)
    except OSError as exc:
        raise YoggError(
            "Не удалось прочитать cookies.txt.",
            f"{type(exc).__name__} (значения cookies не читаются в лог).",
        ) from None

    if stats.get("total", 0) == 0:
        raise YoggError(
            "В cookies.txt нет cookie для vk.com / vk.ru.",
            _vk_cookies_hint() + "\n\n" + _cookie_stats_text(stats),
        )
    if stats.get("session", 0) == 0:
        raise YoggError(
            "В cookies.txt нет сессии VK (remixsid). Обнови файл — сессия мертва или это не тот экспорт.",
            _vk_cookies_hint() + "\n\n" + _cookie_stats_text(stats),
        )

    tools = require_tools()
    ffmpeg = tools["ffmpeg"]

    id_variants = [ref.id_full]
    if ref.access_hash and ref.id_plain not in id_variants:
        id_variants.append(ref.id_plain)
    if ref.id_full not in id_variants:
        id_variants.append(ref.id_full)

    endpoints: list[tuple[str, str, str]] = []
    for host in ("vk.ru", "vk.com"):
        for act, field in (
            ("reload_audios", "audio_ids"),
            ("reload_audio", "ids"),
            ("reload_audio", "audio_ids"),
            ("reload_audios", "ids"),
        ):
            endpoints.append((f"https://{host}/al_audio.php", act, field))
        endpoints.append((f"https://m.{host}/audio", "reload_audio", "ids"))

    attempts_log: list[str] = []
    user_id: int | None = None
    last_status = 0
    last_shape: Any = None
    saw_login = False
    stream_url = ""
    artist = ""
    title = ""

    if progress:
        progress(0.08)
    status("VK: запрашиваю поток через API…")

    # Warm-up: detect dead session / learn user id. Never fatal, never log body.
    for warm_url in ("https://vk.ru/", "https://vk.com/"):
        warm_host = urlparse(warm_url).netloc
        try:
            wh = {
                "User-Agent": CHROME_UA,
                "Accept": "text/html,application/xhtml+xml",
                "Cookie": _cookie_header(rows, warm_host),
            }
            st, body, final = _http_request("GET", warm_url, data=None, headers=wh, timeout=25)
            last_status = st
            if "login" in (final or "").lower() or st in (401, 403):
                saw_login = True
            uid = _extract_vk_user_id(body)
            if uid:
                user_id = uid
                saw_login = False
                break
        except Exception:
            continue

    n_ep = max(1, len(endpoints) * len(id_variants))
    done = 0
    for endpoint, act, field in endpoints:
        host = urlparse(endpoint).netloc
        cookie_hdr = _cookie_header(rows, host)
        headers = {
            "User-Agent": CHROME_UA,
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": f"https://{host.split(':')[0].lstrip('m.') if False else host.replace('m.','')}",
            "Referer": f"https://{host.replace('m.','')}/",
            "Cookie": cookie_hdr,
        }
        # Normalize origin/referer to vk.com / vk.ru without m.
        root_host = host.replace("m.", "")
        headers["Origin"] = f"https://{root_host}"
        headers["Referer"] = f"https://{root_host}/"
        for audio_ids in id_variants:
            done += 1
            if progress:
                progress(min(0.5, 0.1 + 0.4 * done / n_ep))
            payload = urllib.parse.urlencode(
                {"act": act, "al": "1", field: audio_ids}
            ).encode("ascii")
            try:
                st, body, final = _http_request(
                    "POST", endpoint, data=payload, headers=headers, timeout=30,
                )
            except YoggError as exc:
                attempts_log.append(f"{act}/{field} @{host}: {exc.message}")
                continue
            last_status = st
            if "login" in (final or "").lower():
                saw_login = True
            parsed = _parse_vk_ajax(body)
            last_shape = _json_shape(parsed)
            if st in (401, 403):
                saw_login = True
                attempts_log.append(f"{act}/{field} @{host}: HTTP {st}")
                continue
            if st != 200:
                attempts_log.append(f"{act}/{field} @{host}: HTTP {st}, body={len(body)}b")
                continue
            text_low = body.decode("utf-8", errors="replace")[:800].lower()
            if any(x in text_low for x in ("login", "войдите", "auth", "captcha", "security check")):
                # login page / captcha — do not dump body
                if "payload" not in text_low and "audio" not in text_low:
                    saw_login = True
                    attempts_log.append(f"{act}/{field} @{host}: похоже на логин/капчу (len={len(body)})")
                    continue
            items: list[list] = []
            _walk_audio_items(parsed, items)
            if isinstance(parsed, dict):
                pl = parsed.get("payload")
                if isinstance(pl, list) and pl and pl[0] not in (0, "0", None):
                    attempts_log.append(
                        f"{act}/{field} @{host}: payload_code={pl[0]!s} items={len(items)}"
                    )
            chosen = None
            for it in items:
                u = it[2] if len(it) > 2 and isinstance(it[2], str) else ""
                if u:
                    chosen = it
                    break
            if chosen is None and items:
                chosen = items[0]
            if chosen is None:
                attempts_log.append(
                    f"{act}/{field} @{host}: HTTP {st} items=0 shape={last_shape}"
                )
                continue
            stream_url, artist, title = _pick_stream(chosen, user_id)
            if stream_url:
                attempts_log.append(f"{act}/{field} @{host}: ok url_len={len(stream_url)}")
                break
            attempts_log.append(
                f"{act}/{field} @{host}: пустой url (title_len={len(title)})"
            )
        if stream_url:
            break

    if not stream_url:
        extra = _cookie_stats_text(stats)
        shape = f"последний HTTP {last_status}; форма ответа: {last_shape}"
        if saw_login or last_status in (401, 403):
            raise YoggError(
                "Сессия VK недействительна или истекла. Обнови cookies.txt и попробуй снова.",
                extra + "\n" + shape + "\n" + "\n".join(attempts_log[-8:]),
            )
        raise YoggError(
            "VK не отдал ссылку на поток аудио.",
            extra + "\n" + shape + "\n" + "\n".join(attempts_log[-10:]) + "\n\n" + _vk_cookies_hint(),
        )

    # Prefer a direct mp3 if the m3u8 rewrite is available; ffmpeg can do either.
    candidates = [stream_url]
    alt = _maybe_mp3_from_m3u8(stream_url)
    if alt != stream_url:
        candidates.insert(0, alt)

    label = " - ".join(p for p in (artist, title) if p) or f"vk_{ref.id_plain}"
    dest = unique_path(dest_dir / f"{safe_stem(label)}.mp3")
    last_ff_err = ""
    for cand in candidates:
        try:
            _ffmpeg_to_mp3(
                cand,
                dest,
                ffmpeg,
                extra_headers={"Referer": "https://vk.com/", "User-Agent": CHROME_UA},
                status=status,
                progress=progress,
            )
            if dest.is_file() and dest.stat().st_size > 1000:
                if progress:
                    progress(1.0)
                return DownloadResult(
                    path=dest,
                    title=label,
                    host="vk",
                    used_cookies=True,
                )
        except YoggError as exc:
            last_ff_err = exc.detail or exc.message
            continue
    raise YoggError(
        "Не удалось скачать поток VK в MP3.",
        last_ff_err or "ffmpeg не создал файл",
    )


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

    vk_ref = parse_vk_audio_url(url)
    if vk_ref is not None:
        require_tools()
        log_fn("VK: скачиваю один трек (API, не yt-dlp)…")
        return _download_vk_audio(url, dest_dir, status=log_fn, progress=progress)

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
            log_fn("VK: скачиваю через yt-dlp (видео)…")
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
            "Не удалось скачать с VK (это не /audio… — yt-dlp пробует как видео).",
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
