# -*- coding: utf-8 -*-
from pathlib import Path

root = Path(r"C:\Users\de1ze1\Documents\GitHub\Y-OGG")

# --- y_ogg.py: prefer a yt-dlp that actually runs --version ---
p = root / "y_ogg.py"
text = p.read_text(encoding="utf-8")
old = """def find_exe(name: str) -> Path | None:
    names = [name]
    if os.name == "nt" and not name.lower().endswith(".exe"):
        names.insert(0, name + ".exe")
    for folder in _bin_dirs():
        for n in names:
            p = folder / n
            if p.is_file():
                return p
    which = shutil.which(name) or (shutil.which(name + ".exe") if os.name == "nt" else None)
    return Path(which) if which else None
"""
new = '''_YTDLP_OK: dict[str, bool] = {}


def _ytdlp_runs(path: Path) -> bool:
    """True when yt-dlp --version succeeds (skip broken PyInstaller builds)."""
    key = str(path).lower()
    cached = _YTDLP_OK.get(key)
    if cached is not None:
        return cached
    ok = False
    try:
        proc = run_cmd([str(path), "--version"], timeout=20)
        blob = (_decode(proc.stdout) + _decode(proc.stderr)).strip()
        ok = proc.returncode == 0 and bool(re.search(r"\\d{4}\\.\\d{2}", blob))
    except Exception:
        ok = False
    _YTDLP_OK[key] = ok
    return ok


def find_exe(name: str) -> Path | None:
    names = [name]
    if os.name == "nt" and not name.lower().endswith(".exe"):
        names.insert(0, name + ".exe")
    want_ytdlp = "yt-dlp" in name.lower()
    for folder in _bin_dirs():
        for n in names:
            p = folder / n
            if p.is_file():
                if want_ytdlp and not _ytdlp_runs(p):
                    continue
                return p
    which = shutil.which(name) or (shutil.which(name + ".exe") if os.name == "nt" else None)
    if which:
        p = Path(which)
        if want_ytdlp and not _ytdlp_runs(p):
            return None
        return p
    return None
'''
if old not in text:
    raise SystemExit("find_exe block not found in y_ogg.py")
p.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
print("patched y_ogg.py find_exe")

# --- yogg_gui.py ---
g = root / "yogg_gui.py"
gt = g.read_text(encoding="utf-8")
old_imp = """from yogg_download import (
    SUPPORTED_HINT,
    default_downloads_dir,
    detect_host,
    download_audio_mp3,
    host_label,
    looks_like_url,
    normalize_url,
    playlist_choice_needed,
    require_ytdlp,
)
"""
new_imp = """from yogg_download import (
    SUPPORTED_HINT,
    default_downloads_dir,
    detect_host,
    download_audio_mp3,
    host_label,
    is_vk_audio_url,
    looks_like_url,
    normalize_url,
    playlist_choice_needed,
    require_ytdlp,
)
"""
if old_imp not in gt:
    raise SystemExit("import block not found in yogg_gui.py")
gt = gt.replace(old_imp, new_imp, 1)

old_hint = '''            text=f"YouTube, SoundCloud и VK. Другие сайты — тоже через yt-dlp.",
'''
new_hint = '''            text="YouTube и SoundCloud — yt-dlp. Аудио VK (vk.ru/audio…) — API VK + cookies.txt; видео VK — yt-dlp.",
'''
if old_hint not in gt:
    raise SystemExit("url hint not found")
gt = gt.replace(old_hint, new_hint, 1)

old_req = """        try:
            require_ytdlp()
            require_tools()
        except YoggError as exc:
            messagebox.showerror(APP_NAME, exc.full(), parent=self)
            return
"""
new_req = """        try:
            if not is_vk_audio_url(url):
                require_ytdlp()
            require_tools()
        except YoggError as exc:
            messagebox.showerror(APP_NAME, exc.full(), parent=self)
            return
"""
if old_req not in gt:
    raise SystemExit("require_ytdlp block not found")
gt = gt.replace(old_req, new_req, 1)

old_st = '''        self.dl_status_var.set("Запускаю yt-dlp…")
'''
new_st = '''        self.dl_status_var.set(
            "Запускаю скачивание VK…" if is_vk_audio_url(url) else "Запускаю yt-dlp…"
        )
'''
if old_st not in gt:
    raise SystemExit("status line not found")
gt = gt.replace(old_st, new_st, 1)

g.write_text(gt, encoding="utf-8", newline="\n")
print("patched yogg_gui.py")

# --- .gitignore ---
ig = root / ".gitignore"
it = ig.read_text(encoding="utf-8")
if "bin/_internal/" not in it:
    it = it.replace("bin/ffprobe*\n", "bin/ffprobe*\nbin/_internal/\n", 1)
    ig.write_text(it, encoding="utf-8", newline="\n")
    print("patched .gitignore")
else:
    print(".gitignore already has bin/_internal/")

# --- README.md ---
rd = root / "README.md"
rt = rd.read_text(encoding="utf-8")
rt = rt.replace(
    "| **Через ссылку** | Вставь URL, скачай аудио как **MP3** через yt-dlp + ffmpeg |",
    "| **Через ссылку** | Вставь URL, скачай аудио как **MP3** (yt-dlp для YouTube/SoundCloud/видео VK; API VK для аудио) |",
)
rt = rt.replace(
    "Поддерживаемые сайты для ссылок: **YouTube**, **SoundCloud**, **VK (ВКонтакте)**. Другие хосты всё равно пробуются через yt-dlp.",
    "Поддерживаемые сайты для ссылок: **YouTube**, **SoundCloud**, **VK (ВКонтакте)**. YouTube / SoundCloud / *видео* VK — через yt-dlp. Ссылки на *аудио* VK (`vk.ru/audioOWNER_ID_TRACK_ID`, `vk.com/audio…`) качаются через API VK, не через yt-dlp. Другие хосты всё равно пробуются через yt-dlp.",
)
old_ck = """Программа передаёт его в yt-dlp как `--cookies`. **Браузерные cookies сама не забирает.**

- **YouTube**: при 403 пробуются `player_client` web → ios → android, плюс `--force-ipv4`. Cookies помогают, если Google режет анонимные запросы.
- **SoundCloud**: тот же пайплайн yt-dlp.
- **VK / vk.com / vk.ru / vkvideo**: часто нужна авторизация. Если yt-dlp просит логин — положи `cookies.txt` (Netscape) рядом с программой. Без него приватные и многие обычные треки VK не скачаются.
"""
new_ck = """Программа передаёт его в yt-dlp как `--cookies` (YouTube / SoundCloud / видео VK). Для **аудио VK** тот же файл читается самой программой (заголовок Cookie). **Браузерные cookies сама не забирает.** Значения cookies никогда не пишутся в лог и не коммитятся.

- **YouTube**: при 403 пробуются `player_client` web → ios → android, плюс `--force-ipv4`. Cookies помогают, если Google режет анонимные запросы.
- **SoundCloud**: тот же пайплайн yt-dlp.
- **VK audio** (`vk.com/audio…`, `vk.ru/audioOWNER_ID_TRACK_ID[_hash]`): **нужен cookies.txt**. yt-dlp здесь не используется — у него только видео-экстракторы VK, ссылка `/audio619_…` падает как `Unsupported URL`. Y-OGG делает POST на `al_audio.php` (`act=reload_audios` / `reload_audio`) с сессией из cookies и пишет MP3 через ffmpeg. Без файла или с мёртвой сессией будет ошибка на русском, без дампа секретов.
- **VK video** (`vk.com/video…`, vkvideo): по-прежнему yt-dlp + cookies.
"""
if old_ck not in rt:
    raise SystemExit("README cookies block not found")
rt = rt.replace(old_ck, new_ck, 1)
rt = rt.replace(
    "- `yogg_download.py` — yt-dlp MP3 downloader (YouTube / SoundCloud / VK)",
    "- `yogg_download.py` — MP3 downloader (yt-dlp for YouTube / SoundCloud / VK video; native API for VK audio)",
)
rt = rt.replace(
    "Две вкладки: **Через ссылку** качает MP3 с YouTube / SoundCloud / VK (yt-dlp + ffmpeg); **Конвертировать скачанное** — прежний локальный энкодер. Для VK почти всегда нужен `cookies.txt` (Netscape) рядом с программой.",
    "Две вкладки: **Через ссылку** качает MP3 с YouTube / SoundCloud (yt-dlp + ffmpeg) и аудио VK по ссылкам `vk.ru/audioOWNER_ID_TRACK_ID` (API VK + cookies.txt; yt-dlp только для *видео* VK); **Конвертировать скачанное** — прежний локальный энкодер.",
)
rd.write_text(rt, encoding="utf-8", newline="\n")
print("patched README.md")
print("done")
