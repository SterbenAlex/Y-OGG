# Y-OGG

MP3 → **mono OGG Vorbis**, жёсткий потолок **1 572 864 байт (1.5 MiB)**. Кодек только **libvorbis** (не Opus).

Author / автор: **de1ze1 (Вадим Угаров)**. Free to use.

Windows 10/11: double-click `Y-OGG.exe` (CustomTkinter GUI) or run the CLI. Encoder is adaptive libvorbis — never Opus, never negative `-q:a`.

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

## Windows product (1.2)

Ready folder (no Python required):

```
Y-OGG 1.2\
  Y-OGG.exe
  _internal\          ← PyInstaller runtime (onedir)
  bin\ffmpeg.exe
  bin\ffprobe.exe
  Finished\
  Прочитай меня.txt
```

Double-click `Y-OGG.exe`: pick MP3(s) or a folder, choose the output folder (default `Finished` next to the exe), convert. Files land as `{name}_mono.ogg`, max 1.5 MiB.

ffmpeg/ffprobe are **shipped next to the app**, not committed to git. Copy them from a local ffmpeg build into `bin\` after PyInstaller.

---

## Usage (source / CLI)

Put `ffmpeg.exe` / `ffprobe.exe` in one of:

- `bin\` next to `Y-OGG.exe` or `y_ogg.py` — preferred
- `%Y-OGG%\bin`
- `%USERPROFILE%\ffmpeg`

```bat
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

Python **3.12** (not 3.14). From the repo root, with `icon.ico` present:

```powershell
py -3.12 -m pip install customtkinter pyinstaller
py -3.12 -m PyInstaller --noconfirm --clean Y-OGG.spec
```

Then copy `ffmpeg.exe` and `ffprobe.exe` into `dist\Y-OGG\bin\`, create `dist\Y-OGG\Finished\`, and add `Прочитай меня.txt`. Do **not** add those exes to git.

The frozen exe finds `bin\` via `Path(sys.executable).parent / "bin"` (`yogg_root()` / `_script_dir()`).

---

## Repo

- `y_ogg.py` / `yogg.cmd` — CLI converter (no args → GUI)
- `yogg_gui.py` — CustomTkinter GUI (calls `convert_file(..., mode="new")`)
- `Y-OGG.spec` — PyInstaller onedir spec
- `bench.py` — batch + report
- `bench/report.md`, `bench/results.csv` — last bench (committed)
- `Test files/` — 18 mixed-genre MP3s (already in the repo)
- `icon.ico` — window / exe icon

Do **not** commit `ffmpeg.exe`, `yt-dlp`, `Y-OGG.exe`, encoded OGGs, `build/`, or `dist/`.

---

## Русский

Конвертер MP3 в моно OGG Vorbis с жёстким лимитом 1.5 МиБ. Новый энкодер не набивает файл до потолка: берёт битрейт из длительности с запасом, держит 32/44.1 кГц на коротких треках и опускает частоту / q / CBR только когда иначе не влезть. Даунмикс — равная мощность каналов, плюс cutoff сверху. Отрицательный q в ffmpeg не передаётся.

Готовый продукт — папка с `Y-OGG.exe`: двойной щелчок, выбор MP3 или папки, результат в `Finished`.
