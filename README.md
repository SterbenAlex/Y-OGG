# Y-OGG

MP3 → **mono OGG Vorbis**, жёсткий потолок **1 572 864 байт (1.5 MiB)**. Кодек только **libvorbis** (не Opus).

Author / автор: **de1ze1 (Вадим Угаров)**. Free to use.

This pass is a CLI converter + bench. YouTube / GUI are not required here.

---

## Encoder (new)

1. `ffprobe` — duration, sample rate, channels.
2. Target bitrate from duration with headroom:
   `budget_kbps = (1.5 MiB × 8 × 0.88) / duration`, then cap at ~58 kbps so short tracks are **not** packed to 1.5 MB.
3. Adaptive sample rate: **44100 / 32000** for short/simple, **22050 / 16000** only when duration/density needs it.
4. Stereo → mono via `pan=mono|c0=0.5*c0+0.5*c1` (equal power), plus encoder `-cutoff` so bits are not spent on inaudible HF.
5. Constrained VBR (`-q:a`). If still over the cap: lower q, then rate, last resort managed CBR (`-b:a`).

A change is better if: (1) more files ≤ 1.5 MiB, then (2) smaller size at similar/better quality, then (3) better quality at similar size. Source MP3 size is **not** a quality proxy.

### Modes

| mode | what |
|------|------|
| `new` (default) | adaptive plan above |
| `baseline` | `ffmpeg -ac 1 -ar 16000 -c:a libvorbis -q:a 0` |
| `old` | always-16 kHz integer q binary search filling ≤ 1.5 MiB |

The original GUI encoder searched integer q in `[-2, 8]` at 16 kHz and passed it as two argv tokens (`-q:a`, `-2`). **Negative q is parsed as a new ffmpeg flag**, so libvorbis kept the default ~q=3 (~70 kbps mono) — *Mick Gordon — Type-03* (323 s) became ~2.75 MB. This tree never passes a negative q (argv would look like a flag). q is `0…4`; anything tighter uses managed CBR (`-b:a`).

---

## Usage

Put `ffmpeg.exe` / `ffprobe.exe` in one of:

- `%Y-OGG%\bin` (e.g. `C:\Users\de1ze1\Desktop\Y-OGG\bin`) — preferred
- `.\bin` next to `y_ogg.py`
- `%USERPROFILE%\ffmpeg`

```bat
yogg.cmd "track.mp3"
yogg.cmd "D:\music\album" -o "D:\out"
py -3 y_ogg.py "track.mp3" -o "track_mono.ogg"
py -3 y_ogg.py "Test files" -o "Finished" --mode new
```

Bench (writes `bench/report.md` + `bench/results.csv`; OGGs stay in `bench/out/` and are gitignored):

```bat
py -3 bench.py
py -3 bench.py --modes baseline,old,new --jobs 3
```

Hard cap constant: `LIMIT_BYTES = 1_572_864` in `y_ogg.py`.

---

## Repo

- `y_ogg.py` / `yogg.cmd` — CLI converter
- `bench.py` — batch + report
- `bench/report.md`, `bench/results.csv` — last bench (committed)
- `Test files/` — 18 mixed-genre MP3s (already in the repo)

Do **not** commit `ffmpeg.exe`, `yt-dlp`, `Y-OGG.exe`, or encoded OGGs.

---

## Русский

Конвертер MP3 в моно OGG Vorbis с жёстким лимитом 1.5 МиБ. Новый энкодер не набивает файл до потолка: берёт битрейт из длительности с запасом, держит 32/44.1 кГц на коротких треках и опускает частоту / q / CBR только когда иначе не влезть. Даунмикс — равная мощность каналов, плюс cutoff сверху.

Эта ветка — CLI и бенч. GUI и YouTube в этом проходе не трогаем.
