# Y-OGG

MP3 → **mono OGG Vorbis**, жёсткий потолок **1 572 864 байт (1.5 MiB)**. Кодек только **libvorbis** (не Opus).

Author / автор: **de1ze1 (Вадим Угаров)**. Free to use.

Windows 10/11: double-click `Y-OGG.exe` (CustomTkinter GUI) or run the CLI. Encoder is adaptive libvorbis — never Opus, never negative `-q:a`.

---

## GUI 1.4 — две вкладки

Вверху два переключателя (состояние конвертера при переключении **не сбрасывается**):

| Вкладка | Что делает |
|---------|------------|
| **Через ссылку** | Вставь URL, скачай аудио как **MP3** (yt-dlp для YouTube/SoundCloud/видео VK; API VK для аудио) |
| **Конвертировать скачанное** | Локальные MP3 → mono OGG (тот же энкодер `convert_file(..., mode="new")`) |

Поддерживаемые сайты для ссылок: **YouTube**, **SoundCloud**, **VK (ВКонтакте)**. YouTube / SoundCloud / *видео* VK — через yt-dlp. Ссылки на *аудио* VK (`vk.ru/audioOWNER_ID_TRACK_ID`, `vk.com/audio…`) качаются через API VK, не через yt-dlp. Другие хосты всё равно пробуются через yt-dlp.

- Кнопка **Вставить** и **Ctrl+V** работают и на русской раскладке (keycode 86).
- MP3 сохраняются в выбранную папку (по умолчанию `Downloads` рядом с программой, иначе `%USERPROFILE%\Downloads\Y-OGG`).
- Чекбокс **«Сразу конвертировать в OGG»** по умолчанию **выкл**, чтобы вкладки оставались разными. Если включить — после скачивания сразу вызывается тот же энкодер, OGG пишется в папку вкладки конвертера (`Finished`).
- Плейлист целиком **не** качается без подтверждения (по умолчанию один ролик / первый трек).

### cookies.txt

Положи файл **cookies.txt** (формат Netscape) **рядом с программой** — туда же, где `Y-OGG.exe` / `y_ogg.py`:

```
Y-OGG\
  Y-OGG.exe
  cookies.txt     ← опционально
  bin\yt-dlp.exe
  bin\ffmpeg.exe
  bin\ffprobe.exe
```

Программа передаёт его в yt-dlp как `--cookies` (YouTube / SoundCloud / видео VK). Для **аудио VK** тот же файл читается самой программой (заголовок Cookie). **Браузерные cookies сама не забирает.** Значения cookies никогда не пишутся в лог и не коммитятся.

- **YouTube**: при 403 пробуются `player_client` web → ios → android, плюс `--force-ipv4`. Cookies помогают, если Google режет анонимные запросы.
- **SoundCloud**: тот же пайплайн yt-dlp.
- **VK audio** (`vk.com/audio…`, `vk.ru/audioOWNER_ID_TRACK_ID[_hash]`): **нужен cookies.txt**. yt-dlp здесь не используется — у него только видео-экстракторы VK, ссылка `/audio619_…` падает как `Unsupported URL`. Y-OGG делает POST на `al_audio.php` (`act=reload_audios` / `reload_audio`) с сессией из cookies и пишет MP3 через ffmpeg. Без файла или с мёртвой сессией будет ошибка на русском, без дампа секретов.
- **VK video** (`vk.com/video…`, vkvideo): по-прежнему yt-dlp + cookies.

---

## Encoder (new)

1. `ffprobe` — duration, sample rate, channels.
2. Target bitrate from duration with headroom:
   `budget_kbps = (1.5 MiB × 8 × 0.85) / duration`, then cap at ~64 kbps so short tracks are **not** packed to 1.5 MB.
3. Adaptive sample rate: **44100 / 32000** for short/simple, **22050 / 16000** only when duration/density needs it.
4. Stereo → mono via `pan=mono|c0=0.5*c0+0.5*c1` (equal power), plus encoder `-cutoff` so bits are not spent on inaudible HF.
5. Constrained VBR (`-q:a`, always ≥ 0). If still over the cap: lower q, then rate, last resort managed CBR (`-b:a`).

A change is better if: (1) more files ≤ 1.5 MiB, then (2) smaller size at similar/better quality, then (3) better quality at similar size. Source MP3 size is **not** a quality proxy.

### Modes

| mode | what |
|------|------|
| `new` (default) | adaptive plan above |
| `baseline` | `ffmpeg -ac 1 -ar 16000 -c:a libvorbis -q:a 0` |
| `old` | always-16 kHz integer q binary search filling ≤ 1.5 MiB |

The original GUI encoder searched integer q in `[-2, 8]` at 16 kHz and passed it as two argv tokens (`-q:a`, `-2`). **Negative q is parsed as a new ffmpeg flag**, so libvorbis kept the default ~q=3 (~70 kbps mono) — *Mick Gordon — Type-03* (323 s) became ~2.75 MB. This tree never passes a negative q (argv would look like a flag). q is `0…4`; anything tighter uses managed CBR (`-b:a`).

---

## Windows product (1.4)

Ready folder (no Python required):

```
Y-OGG 1.4\
  Y-OGG.exe
  _internal\          ← PyInstaller runtime (onedir)
  bin\ffmpeg.exe
  bin\ffprobe.exe
  bin\yt-dlp.exe
  Finished\
  Downloads\
  Прочитай меня.txt
```

Double-click `Y-OGG.exe`: вкладка «Через ссылку» качает MP3, вкладка «Конвертировать скачанное» берёт локальные файлы. OGG: `{name}_mono.ogg`, max 1.5 MiB.

ffmpeg / ffprobe / yt-dlp **лежат рядом с приложением**, в git не коммитятся. После PyInstaller скопируй их в `bin\`.

---

## Обновления (1.4)

**Программа.** При запуске Y-OGG в фоне (таймаут ~6 с, без ошибок при отсутствии сети)
проверяет последний релиз `SterbenAlex/Y-OGG` на GitHub и сравнивает тег (`1.4.0`, `v1.2.0`)
с текущей версией. Если есть новее — спрашивает «Доступна версия X. Обновить?». При «Да»
скачивается `Y-OGG.zip` из релиза, распаковывается во временную папку, и маленький
скрипт `yogg_updater.cmd` ждёт закрытия программы, копирует новые файлы поверх папки
(robocopy без удаления, **не трогая** `cookies.txt`, `yogg_settings.json`, `Downloads\`,
`Finished\`) и снова запускает `Y-OGG.exe`. Лог: `%TEMP%\yogg_updater.log`.
Автообновление работает только в собранной версии; из исходников программа лишь сообщит о релизе.

**yt-dlp.** Если `bin\yt-dlp.exe` старше 7 дней — при запуске в фоне выполняется
`yt-dlp -U` (скрытое окно). Если скачивание падает с ошибкой извлечения (YouTube что-то
поменял), yt-dlp обновляется один раз и скачивание повторяется автоматически.

**Кнопка «Обновления ▾»** внизу окна: «Проверить обновления», «Обновить yt-dlp»,
галочки «Проверять обновления при запуске» и «Автообновление yt-dlp».
Настройки хранятся в `yogg_settings.json` рядом с программой.

Для релизов: ассет должен называться `Y-OGG.zip` (любой `.zip` тоже подойдёт) и содержать `Y-OGG.exe`.

## Usage (source / CLI)

Положи `ffmpeg.exe`, `ffprobe.exe` и `yt-dlp.exe` в одно из:

- `bin\` next to `Y-OGG.exe` or `y_ogg.py` — preferred
- `%Y-OGG%\bin`
- `%USERPROFILE%\Desktop\Y-OGG\bin`
- `%USERPROFILE%\ffmpeg`

```bat
py -3.12 yogg_gui.py
py -3.12 y_ogg.py
py -3.12 y_ogg.py "track.mp3"
py -3.12 y_ogg.py "D:\music\album" -o "D:\out"
yogg.cmd "track.mp3"
```

No arguments → GUI (needs `customtkinter`). With a file/folder → CLI.

```bat
py -3.12 -m pip install -r requirements.txt
py -3.12 y_ogg.py --gui
```

Bench (writes `bench/report.md` + `bench/results.csv`; OGGs stay in `bench/out/` and are gitignored):

```bat
py -3 bench.py
py -3 bench.py --modes baseline,old,new --jobs 3
```

Hard cap constant: `LIMIT_BYTES = 1_572_864` in `y_ogg.py`.

---

## Build the exe (PyInstaller onedir)

Python **3.12** (not 3.14). From the repo root, with `icon.ico` present. If `bin\yt-dlp.exe` exists, `Y-OGG.spec` подхватит его (`--add-binary bin/yt-dlp.exe;bin`). ffmpeg по-прежнему копируется после сборки.

```powershell
py -3.12 -m pip install customtkinter pyinstaller
py -3.12 -m PyInstaller --noconfirm --clean Y-OGG.spec
```

Then copy `ffmpeg.exe`, `ffprobe.exe` and `yt-dlp.exe` into `dist\Y-OGG\bin\`, create `dist\Y-OGG\Finished\` and `dist\Y-OGG\Downloads\`, and add `Прочитай меня.txt`. Do **not** add those exes to git.

The frozen exe finds `bin\` via `Path(sys.executable).parent / "bin"` (`yogg_root()` / `_script_dir()`).

---

## Repo

- `y_ogg.py` / `yogg.cmd` — CLI converter (no args → GUI)
- `yogg_gui.py` — CustomTkinter GUI (две вкладки; конвертер вызывает `convert_file(..., mode="new")`)
- `yogg_download.py` — MP3 downloader (yt-dlp for YouTube / SoundCloud / VK video; native API for VK audio)
- `Y-OGG.spec` — PyInstaller onedir spec
- `bench.py` — batch + report
- `bench/report.md`, `bench/results.csv` — last bench (committed)
- `Test files/` — 18 mixed-genre MP3s (already in the repo)
- `icon.ico` — window / exe icon

Do **not** commit `ffmpeg.exe`, `yt-dlp`, `Y-OGG.exe`, encoded OGGs, `cookies.txt`, `build/`, or `dist/`.

---

## Русский

Конвертер MP3 в моно OGG Vorbis с жёстким лимитом 1.5 МиБ. Новый энкодер не набивает файл до потолка: берёт битрейт из длительности с запасом, держит 32/44.1 кГц на коротких треках и опускает частоту / q / CBR только когда иначе не влезть. Даунмикс — равная мощность каналов, плюс cutoff сверху. Отрицательный q в ffmpeg не передаётся.

Две вкладки: **Через ссылку** качает MP3 с YouTube / SoundCloud (yt-dlp + ffmpeg) и аудио VK по ссылкам `vk.ru/audioOWNER_ID_TRACK_ID` (API VK + cookies.txt; yt-dlp только для *видео* VK); **Конвертировать скачанное** — прежний локальный энкодер.

Готовый продукт — папка с `Y-OGG.exe`: двойной щелчок, выбор ссылки или MP3, результат в `Downloads` / `Finished`.
