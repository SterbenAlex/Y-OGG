#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Benchmark Y-OGG encoders on the Test files set.

Writes bench/report.md and bench/results.csv. Encoded OGGs go to
bench/out/{baseline,old,new}/ and are gitignored.

Usage (from the repo root, PowerShell):
    py -3 bench.py
    py -3 bench.py --skip-old
    py -3 bench.py --modes baseline,new
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from array import array
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

from y_ogg import (
    LIMIT_BYTES,
    EncodeResult,
    convert_file,
    find_exe,
    probe_audio,
    require_tools,
)

MSK = timezone(timedelta(hours=3))

GENRE_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"type-03|mick gordon", re.I), "industrial/metal"),
    (re.compile(r"hard movement|hard parade", re.I), "hardcore/gabber"),
    (re.compile(r"boys interface|dark headz|drollxd|univxrsel", re.I), "hardcore/gabber"),
    (re.compile(r"bountyhunter|woops", re.I), "hardcore/gabber"),
    (re.compile(r"carpenter|disco zombi", re.I), "synthwave"),
    (re.compile(r"airglow|lone ", re.I), "electronic/ambient"),
    (re.compile(r"crystal of faded|kairi", re.I), "electronic/ambient"),
    (re.compile(r"worry|lonown|slowed", re.I), "electronic/slowed"),
    (re.compile(r"dazegxd|4ever", re.I), "breaks/dnb"),
    (re.compile(r"dolce gabbana|зверев", re.I), "pop/rap"),
    (re.compile(r"вайбмен|пират", re.I), "rap"),
    (re.compile(r"ost|тема дороги|жмурки|возле твоей|князь|владимир", re.I), "soundtrack"),
    (re.compile(r"русская рать|засидели", re.I), "folk/rock"),
    (re.compile(r"revolution|ss13", re.I), "electronic/game"),
    (re.compile(r"under my wheels", re.I), "industrial/rock"),
    (re.compile(r"angelhard", re.I), "hardcore/gabber"),
]


def infer_genre(name: str) -> str:
    for pat, genre in GENRE_RULES:
        if pat.search(name):
            return genre
    return "unknown"


def estimate_bpm(ffmpeg: Path, src: Path, duration_s: float) -> float | None:
    """Onset-flux autocorrelation on a 20 s mono 11 kHz excerpt. No numpy."""
    start = max(0.0, min(duration_s * 0.25, max(0.0, duration_s - 22.0)))
    length = min(20.0, max(8.0, duration_s - start))
    cmd = [
        str(ffmpeg),
        "-hide_banner", "-nostdin", "-loglevel", "error",
        "-ss", f"{start:.2f}",
        "-t", f"{length:.2f}",
        "-i", str(src),
        "-ac", "1",
        "-ar", "11025",
        "-f", "s16le",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    raw = proc.stdout or b""
    if len(raw) < 11025 * 4:
        return None
    samples = array("h")
    samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
    hop = 256
    n = len(samples)
    env: list[float] = []
    prev = 0.0
    i = 0
    while i + hop <= n:
        acc = 0
        for s in samples[i:i + hop]:
            acc += abs(s)
        val = acc / hop
        flux = val - prev
        env.append(flux if flux > 0 else 0.0)
        prev = val
        i += hop
    if len(env) < 40:
        return None
    fps = 11025.0 / hop
    # BPM 70–180 → lag in frames
    min_lag = max(1, int(fps * 60.0 / 180.0))
    max_lag = min(len(env) - 2, int(fps * 60.0 / 70.0))
    if max_lag <= min_lag + 2:
        return None
    best_lag = min_lag
    best_score = -1.0
    mean = sum(env) / len(env)
    norm = [x - mean for x in env]
    for lag in range(min_lag, max_lag + 1):
        score = 0.0
        count = len(norm) - lag
        for j in range(count):
            score += norm[j] * norm[j + lag]
        score /= count
        if score > best_score:
            best_score = score
            best_lag = lag
    if best_score <= 0:
        return None
    bpm = 60.0 * fps / best_lag
    # Fold into 70–180
    while bpm < 70:
        bpm *= 2
    while bpm > 180:
        bpm /= 2
    return round(bpm, 1)


def ebur128_integrated(ffmpeg: Path, path: Path) -> str | None:
    cmd = [
        str(ffmpeg),
        "-hide_banner", "-nostdin",
        "-i", str(path),
        "-af", "ebur128=peak=true",
        "-f", "null",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stderr or b"").decode("utf-8", errors="replace")
    # Last "I:" in the summary is integrated loudness.
    matches = re.findall(r"\bI:\s*(-?[\d.]+)\s*LUFS", text)
    lra = re.findall(r"\bLRA:\s*([\d.]+)\s*LU", text)
    if not matches:
        return None
    bits = [f"I={matches[-1]} LUFS"]
    if lra:
        bits.append(f"LRA={lra[-1]} LU")
    return " ".join(bits)


def fmt_mb(n: int) -> str:
    return f"{n / (1024 * 1024):.3f}"


def fmt_bool(v: bool) -> str:
    return "yes" if v else "NO"


def write_report(
    path: Path,
    rows: list[dict],
    modes: list[str],
    elapsed_s: float,
) -> None:
    now = datetime.now(MSK).strftime("%Y-%m-%d %H:%M MSK")
    lines: list[str] = []
    lines.append("# Y-OGG encoder bench")
    lines.append("")
    lines.append(f"- When: {now}")
    lines.append(f"- Hard cap: {LIMIT_BYTES} bytes (1.5 MiB)")
    lines.append(f"- Modes: {', '.join(modes)}")
    lines.append(f"- Wall time: {elapsed_s:.1f} s")
    lines.append("- Encoder: libvorbis in OGG (not Opus)")
    lines.append("- Author: de1ze1 (Вадим Угаров)")
    lines.append("")
    lines.append("## Strategy")
    lines.append("")
    lines.append("**baseline** — `ffmpeg -ac 1 -ar 16000 -c:a libvorbis -q:a 0` (old always-16 kHz floor).")
    lines.append("")
    lines.append("**old** — 16 kHz integer q binary search filling ≤ 1.5 MiB (valid q −1…8).")
    lines.append("")
    lines.append(
        "**new** — probe duration/rate → target kbps from duration with 15% headroom, "
        "capped at ~64 kbps so short tracks are *not* packed to 1.5 MiB → adaptive "
        "44100/32000/22050/16000 → equal-power pan downmix + encoder cutoff → VBR q, "
        "then lower q / rate, last-resort managed CBR."
    )
    lines.append("")

    # Per-mode summary
    lines.append("## Summary")
    lines.append("")
    lines.append("| mode | n | under cap | overflows | median MiB | mean MiB | median kbps | min MiB | max MiB |")
    lines.append("|------|---|-----------|-----------|------------|----------|-------------|---------|---------|")
    for mode in modes:
        subset = [r for r in rows if r["mode"] == mode]
        if not subset:
            continue
        sizes = [r["out_bytes"] for r in subset]
        kbps = [r["out_kbps"] for r in subset]
        under = sum(1 for r in subset if r["under_1_5mb"])
        over = len(subset) - under
        lines.append(
            "| {mode} | {n} | {under} | {over} | {med:.3f} | {mean:.3f} | {mk:.1f} | {mn:.3f} | {mx:.3f} |".format(
                mode=mode,
                n=len(subset),
                under=under,
                over=over,
                med=statistics.median(sizes) / (1024 * 1024),
                mean=statistics.mean(sizes) / (1024 * 1024),
                mk=statistics.median(kbps),
                mn=min(sizes) / (1024 * 1024),
                mx=max(sizes) / (1024 * 1024),
            )
        )
    lines.append("")

    # Overflows
    lines.append("## Overflows (over 1.5 MiB)")
    lines.append("")
    overs = [r for r in rows if not r["under_1_5mb"]]
    if not overs:
        lines.append("None. All encoded files in this run are ≤ 1,572,864 bytes.")
    else:
        lines.append("| mode | file | duration_s | out bytes | out MiB |")
        lines.append("|------|------|------------|-----------|---------|")
        for r in overs:
            lines.append(
                f"| {r['mode']} | {r['filename']} | {r['duration_s']:.2f} | "
                f"{r['out_bytes']} | {fmt_mb(r['out_bytes'])} |"
            )
    lines.append("")

    # Comparison table: one row per file
    files = []
    seen = set()
    for r in rows:
        if r["filename"] not in seen:
            seen.add(r["filename"])
            files.append(r["filename"])

    by = {(r["filename"], r["mode"]): r for r in rows}

    lines.append("## Per-file comparison")
    lines.append("")
    header = (
        "| file | dur s | genre | bpm | src MiB | "
        "base MiB | base Hz | base under | "
        "new MiB | new Hz | new mode | new kbps | new under | vs base |"
    )
    lines.append(header)
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for name in files:
        b = by.get((name, "baseline"))
        n = by.get((name, "new"))
        meta = n or b or by.get((name, "old"))
        if meta is None:
            continue
        base_mb = fmt_mb(b["out_bytes"]) if b else "—"
        base_hz = str(b["sample_rate"]) if b else "—"
        base_ok = fmt_bool(b["under_1_5mb"]) if b else "—"
        new_mb = fmt_mb(n["out_bytes"]) if n else "—"
        new_hz = str(n["sample_rate"]) if n else "—"
        new_mode = n["enc_mode"] if n else "—"
        new_kbps = f"{n['out_kbps']:.1f}" if n else "—"
        new_ok = fmt_bool(n["under_1_5mb"]) if n else "—"
        if b and n and b["out_bytes"] > 0:
            delta = (n["out_bytes"] - b["out_bytes"]) / b["out_bytes"] * 100.0
            vs = f"{delta:+.0f}%"
        else:
            vs = "—"
        bpm = meta.get("bpm_est") or "—"
        lines.append(
            f"| {name} | {meta['duration_s']:.1f} | {meta['genre']} | {bpm} | "
            f"{fmt_mb(meta['src_bytes'])} | {base_mb} | {base_hz} | {base_ok} | "
            f"{new_mb} | {new_hz} | {new_mode} | {new_kbps} | {new_ok} | {vs} |"
        )
    lines.append("")

    if "old" in modes:
        lines.append("## Old q-search (fill ≤ 1.5 MiB) vs new")
        lines.append("")
        lines.append("| file | old MiB | old q | old under | new MiB | new under |")
        lines.append("|------|---------|-------|-----------|---------|-----------|")
        for name in files:
            o = by.get((name, "old"))
            n = by.get((name, "new"))
            if not o or not n:
                continue
            lines.append(
                f"| {name} | {fmt_mb(o['out_bytes'])} | {o['enc_mode']} | "
                f"{fmt_bool(o['under_1_5mb'])} | {fmt_mb(n['out_bytes'])} | "
                f"{fmt_bool(n['under_1_5mb'])} |"
            )
        lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append("- Source MP3 size is **not** used as a quality proxy.")
    lines.append("- `vs base` is size delta of **new vs baseline q=0 @ 16 kHz** (negative = smaller).")
    lines.append("- Short tracks should keep 32/44.1 kHz; long tracks may drop to 16 kHz + CBR.")
    lines.append("- BPM is a simple onset-flux autocorrelation (ballpark, not a DAW click-track).")
    lines.append("- Encoded OGGs are not committed; this report is.")
    lines.append("")
    lines.append("## Full rows")
    lines.append("")
    lines.append(
        "| mode | file | dur s | src B | out B | kbps | Hz | enc | bpm | genre | under | notes |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        notes = (r.get("notes") or "").replace("|", "/")
        if len(notes) > 80:
            notes = notes[:77] + "…"
        lines.append(
            f"| {r['mode']} | {r['filename']} | {r['duration_s']:.2f} | "
            f"{r['src_bytes']} | {r['out_bytes']} | {r['out_kbps']:.1f} | "
            f"{r['sample_rate']} | {r['enc_mode']} | {r.get('bpm_est') or ''} | "
            f"{r['genre']} | {fmt_bool(r['under_1_5mb'])} | {notes} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def result_to_row(mode: str, res: EncodeResult, genre: str, bpm: float | None, ebu: str | None) -> dict:
    notes = res.notes
    if ebu:
        notes = f"{notes}; {ebu}"
    return {
        "mode": mode,
        "filename": res.src.name,
        "duration_s": round(res.duration_s, 3),
        "src_bytes": res.src_bytes,
        "out_bytes": res.out_bytes,
        "out_kbps": res.out_kbps,
        "sample_rate": res.sample_rate,
        "enc_mode": res.mode,
        "q": res.q if res.q is not None else "",
        "cbr_kbps": res.bitrate_kbps if res.bitrate_kbps is not None else "",
        "cutoff_hz": res.cutoff_hz,
        "encodes": res.encodes,
        "bpm_est": bpm if bpm is not None else "",
        "genre": genre,
        "under_1_5mb": bool(res.under_cap),
        "notes": notes,
        "ebur128": ebu or "",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bench Y-OGG on Test files")
    parser.add_argument(
        "--test-dir",
        default=str(Path(__file__).resolve().parent / "Test files"),
    )
    parser.add_argument(
        "--modes",
        default="baseline,new",
        help="Comma list: baseline,old,new  (old is slow)",
    )
    parser.add_argument("--skip-old", action="store_true")
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--ebur128", action="store_true", help="Measure loudness of new encodes")
    args = parser.parse_args(argv)

    repo = Path(__file__).resolve().parent
    test_dir = Path(args.test_dir)
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    if args.skip_old:
        modes = [m for m in modes if m != "old"]

    tools = require_tools()
    files = sorted(
        [p for p in test_dir.iterdir() if p.is_file() and p.suffix.lower() == ".mp3"],
        key=lambda p: p.name.lower(),
    )
    if not files:
        print(f"No MP3s in {test_dir}", file=sys.stderr)
        return 1

    out_root = repo / "bench" / "out"
    for mode in modes:
        (out_root / mode).mkdir(parents=True, exist_ok=True)
    (repo / "bench").mkdir(parents=True, exist_ok=True)

    print(f"ffmpeg  {tools['ffmpeg']}")
    print(f"ffprobe {tools['ffprobe']}")
    print(f"{len(files)} MP3s, modes={modes}, jobs={args.jobs}")

    # Probe + BPM once per source
    meta: dict[str, dict] = {}
    for src in files:
        info = probe_audio(tools["ffprobe"], src)
        genre = infer_genre(src.name)
        print(f"bpm  {src.name} …", flush=True)
        bpm = estimate_bpm(tools["ffmpeg"], src, info.duration_s)
        meta[src.name] = {"genre": genre, "bpm": bpm, "duration": info.duration_s}
        print(f"     {info.duration_s:.1f}s  {genre}  bpm={bpm}", flush=True)

    rows: list[dict] = []
    t0 = time.perf_counter()

    def job(mode: str, src: Path) -> tuple[str, EncodeResult]:
        dst = out_root / mode / (src.stem + ".ogg")
        res = convert_file(src, dst, mode=mode, tools=tools, status=lambda _m: None)
        return mode, res

    work: list[tuple[str, Path]] = [(m, f) for m in modes for f in files]
    if args.jobs <= 1:
        done = [job(m, f) for m, f in work]
    else:
        done = []
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            futs = [pool.submit(job, m, f) for m, f in work]
            for fut in as_completed(futs):
                mode, res = fut.result()
                flag = "OK" if res.under_cap else "OVER"
                print(
                    f"{flag:4} {mode:9} {res.src.name}  "
                    f"{res.out_bytes/1048576:.3f} MiB  {res.mode}  {res.sample_rate} Hz",
                    flush=True,
                )
                done.append((mode, res))

    # Preserve a stable order: mode order then filename
    done.sort(key=lambda x: (modes.index(x[0]) if x[0] in modes else 99, x[1].src.name.lower()))

    for mode, res in done:
        m = meta[res.src.name]
        ebu = None
        if args.ebur128 and mode == "new" and res.output.is_file():
            ebu = ebur128_integrated(tools["ffmpeg"], res.output)
        rows.append(result_to_row(mode, res, m["genre"], m["bpm"], ebu))
        if args.jobs <= 1:
            flag = "OK" if res.under_cap else "OVER"
            print(
                f"{flag:4} {mode:9} {res.src.name}  "
                f"{res.out_bytes/1048576:.3f} MiB  {res.mode}  {res.sample_rate} Hz",
                flush=True,
            )

    elapsed = time.perf_counter() - t0
    csv_path = repo / "bench" / "results.csv"
    md_path = repo / "bench" / "report.md"
    fields = [
        "mode", "filename", "duration_s", "src_bytes", "out_bytes", "out_kbps",
        "sample_rate", "enc_mode", "q", "cbr_kbps", "cutoff_hz", "encodes",
        "bpm_est", "genre", "under_1_5mb", "ebur128", "notes",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    write_report(md_path, rows, modes, elapsed)
    print(f"wrote {md_path}")
    print(f"wrote {csv_path}")
    print(f"elapsed {elapsed:.1f}s")

    new_overs = [r for r in rows if r["mode"] == "new" and not r["under_1_5mb"]]
    if new_overs:
        print("NEW encoder overflows:", file=sys.stderr)
        for r in new_overs:
            print(f"  {r['filename']} {r['out_bytes']}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
