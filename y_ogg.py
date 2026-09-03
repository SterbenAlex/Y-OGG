#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Y-OGG — MP3 → mono OGG Vorbis with a hard 1.5 MiB cap.

Author: de1ze1 (Vadim Ugarov / Вадим Угаров). Free to use.

CLI (`python y_ogg.py file.mp3`) and CustomTkinter GUI (no args / --gui).
Encoder is libvorbis in OGG — never Opus. GUI always calls convert_file
with mode="new" (adaptive rate/q, q never negative).

Strategy
--------
1. Probe duration, sample rate, channels (ffprobe).
2. Budget bitrate from duration with headroom so VBR cannot kiss the cap:
       budget_kbps = (LIMIT * 8 * 0.88) / duration
   Then cap the bitrate at ~58 kbps even when the budget is huge, so
   short tracks stay well under 1.5 MiB instead of packing the file.
3. Pick sample rate from that target (44100 / 32000 / 22050 / 16000),
   never above the source. Drop rate only when the budget requires it.
4. Equal-power stereo→mono downmix + encoder cutoff (lowpass) so bits
   are not spent on inaudible HF.
5. Encode constrained VBR (q). If over the cap: lower q, then rate,
   last resort managed CBR (-b:a).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "Y-OGG"
APP_VERSION = "1.2.0"
AUTHOR = "de1ze1 (Вадим Угаров)"

# Hard cap: 1.5 MiB exactly.
LIMIT_BYTES = 1_572_864
# VBR overshoot margin used when choosing the first encode.
HEADROOM = 0.85
# Even if the budget allows more, do not spend above this (keeps short
# tracks smaller than a naive "fill 1.5 MB" q-search).
PREF_KBPS = 64.0
MAX_Q = 4.0
MIN_Q = 0.0
SAMPLE_RATES = (44100, 32000, 22050, 16000)
AUDIO_SUFFIXES = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aiff", ".aif"}

CREATE_NO_WINDOW = 0x08000000


# ---------------------------------------------------------------------------
# Errors / process helpers
# ---------------------------------------------------------------------------

class YoggError(Exception):
    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or ""

    def full(self) -> str:
        return f"{self.message}\n{self.detail}".strip()


def _hidden_kwargs() -> dict:
    kw: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW)
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        kw["startupinfo"] = si
    return kw


def run_cmd(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(cmd, timeout=timeout, **_hidden_kwargs())


def _decode(blob: bytes | None) -> str:
    if not blob:
        return ""
    return blob.decode("utf-8", errors="replace")


def tail(text: str, limit: int = 1800) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return "…\n" + text[-limit:]


# ---------------------------------------------------------------------------
# ffmpeg / ffprobe discovery
# ---------------------------------------------------------------------------

def _script_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def yogg_root() -> Path:
    """Folder that contains the app (and bin/, Finished/).

    Frozen: directory of Y-OGG.exe so the product is portable.
    Env Y-OGG / Y_OGG is still searched for ffmpeg in _bin_dirs().
    """
    return _script_dir()


def default_finished_dir() -> Path:
    d = yogg_root() / "Finished"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _bin_dirs() -> list[Path]:
    out: list[Path] = []
    for key in ("Y-OGG", "Y_OGG"):
        env = os.environ.get(key)
        if env:
            out.append(Path(env) / "bin")
            out.append(Path(env))
    here = yogg_root()
    out.extend(
        [
            here / "bin",
            here,
            _script_dir() / "bin",
            _script_dir(),
            Path.home() / "Desktop" / "Y-OGG" / "bin",
            Path.home() / "ffmpeg" / "bin",
            Path.home() / "ffmpeg",
        ]
    )
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def find_exe(name: str) -> Path | None:
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


def require_tools() -> dict[str, Path]:
    found: dict[str, Path] = {}
    missing: list[str] = []
    for name in ("ffmpeg", "ffprobe"):
        p = find_exe(name)
        if p is None:
            missing.append(name + (".exe" if os.name == "nt" else ""))
        else:
            found[name] = p
    if missing:
        roots = "\n".join(f"  • {c}" for c in _bin_dirs())
        raise YoggError(
            "ffmpeg/ffprobe not found.",
            "Missing:\n  • "
            + "\n  • ".join(missing)
            + f"\n\nLooked in:\n{roots}\n\n"
            r"Put ffmpeg.exe and ffprobe.exe in %Y-OGG%\bin "
            r"(e.g. C:\Users\de1ze1\Desktop\Y-OGG\bin).",
        )
    return found


# ---------------------------------------------------------------------------
# Probe / names
# ---------------------------------------------------------------------------

@dataclass
class Probe:
    duration_s: float
    sample_rate: int
    channels: int
    codec: str = ""
    bit_rate: int = 0
    size_bytes: int = 0


def probe_audio(ffprobe: Path, path: Path) -> Probe:
    cmd = [
        str(ffprobe),
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-select_streams", "a:0",
        str(path),
    ]
    proc = run_cmd(cmd)
    if proc.returncode != 0:
        raise YoggError("ffprobe could not read the file.", tail(_decode(proc.stderr)))
    try:
        data = json.loads(_decode(proc.stdout) or "{}")
    except json.JSONDecodeError as exc:
        raise YoggError("ffprobe returned non-JSON.", str(exc)) from exc

    fmt = data.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    try:
        size_bytes = int(fmt.get("size") or 0)
    except (TypeError, ValueError):
        size_bytes = 0
    try:
        bit_rate = int(fmt.get("bit_rate") or 0)
    except (TypeError, ValueError):
        bit_rate = 0

    rate = 0
    channels = 0
    codec = ""
    for stream in data.get("streams") or []:
        try:
            rate = int(stream.get("sample_rate") or 0)
        except (TypeError, ValueError):
            rate = 0
        try:
            channels = int(stream.get("channels") or 0)
        except (TypeError, ValueError):
            channels = 0
        codec = str(stream.get("codec_name") or "")
        if duration <= 0:
            try:
                duration = float(stream.get("duration") or 0)
            except (TypeError, ValueError):
                pass
        if rate:
            break

    if duration <= 0.05:
        raise YoggError("Could not determine audio duration.")
    if not path.is_file():
        raise YoggError(f"File not found: {path}")
    if size_bytes <= 0:
        try:
            size_bytes = path.stat().st_size
        except OSError:
            size_bytes = 0
    return Probe(duration, rate, channels, codec, bit_rate, size_bytes)


_WIN_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_stem(raw: str, limit: int = 80) -> str:
    name = _WIN_BAD.sub("_", (raw or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "audio"
    if len(name) > limit:
        name = name[:limit].rstrip(" .")
    return name


def default_output_path(src: Path, out_dir: Path | None) -> Path:
    folder = out_dir if out_dir is not None else src.parent
    return folder / f"{safe_stem(src.stem)}_mono.ogg"


# ---------------------------------------------------------------------------
# Bitrate / q / rate plan
# ---------------------------------------------------------------------------

# Dense-material q=0 mono kbps measured on the 18-file set (gabber/industrial
# overshoots the textbook vorbis table, especially above 16 kHz).
Q0_KBPS = {
    44100: 68.0,
    32000: 56.0,
    22050: 40.0,
    16000: 28.0,
    12000: 22.0,
    11025: 20.0,
    8000: 16.0,
}


def nominal_mono_kbps(q: float, rate: int) -> float:
    """Conservative libvorbis mono kbps from q and sample rate."""
    nearest = min(Q0_KBPS, key=lambda r: abs(r - rate))
    base = Q0_KBPS[nearest]
    if rate not in Q0_KBPS and rate > 0:
        base = base * (rate / nearest)
    return base * (2.0 ** (q / 4.0))


def budget_kbps(duration_s: float, headroom: float = HEADROOM) -> float:
    if duration_s <= 0:
        return PREF_KBPS
    return (LIMIT_BYTES * 8.0 * headroom) / duration_s / 1000.0


def cutoff_for_rate(rate: int) -> int:
    # Keep a little air above Nyquist*0.45 so Vorbis has room for the
    # transition band, but do not spend bits on inaudible HF.
    table = {
        44100: 16000,
        32000: 14000,
        22050: 9800,
        16000: 7200,
        12000: 5400,
        11025: 5000,
        8000: 3500,
    }
    return table.get(rate, max(3500, int(rate * 0.45)))


def usable_rates(source_rate: int) -> tuple[int, ...]:
    if source_rate <= 0:
        return SAMPLE_RATES
    rates = tuple(r for r in SAMPLE_RATES if r <= source_rate)
    return rates or (min(SAMPLE_RATES[-1], source_rate),)


def q_for_target(target_kbps: float, rate: int) -> float:
    """Largest q in 0.5 steps whose estimate stays under target_kbps."""
    chosen = MIN_Q
    q = MIN_Q
    while q <= MAX_Q + 1e-9:
        if nominal_mono_kbps(q, rate) <= target_kbps:
            chosen = q
        q += 0.5
    return round(chosen, 1)


@dataclass
class EncodePlan:
    sample_rate: int
    q: float | None
    bitrate_kbps: int | None
    cutoff_hz: int
    estimated_kbps: float
    reason: str

    @property
    def mode_label(self) -> str:
        if self.bitrate_kbps is not None:
            return f"cbr{self.bitrate_kbps}k"
        return f"q={self.q:.1f}"


def _pick_rate(wanted: int, rates: tuple[int, ...]) -> int:
    for r in rates:
        if r <= wanted:
            return r
    return rates[-1]


def cbr_plan(duration_s: float, source_rate: int, headroom: float = 0.84, reason: str = "cbr") -> EncodePlan:
    rates = usable_rates(source_rate)
    rate = 16000 if 16000 in rates else rates[-1]
    kbps = max(16, int(budget_kbps(duration_s, headroom)))
    return EncodePlan(rate, None, kbps, cutoff_for_rate(rate), float(kbps), reason)


def plan_new(duration_s: float, source_rate: int) -> EncodePlan:
    """Quality-first plan that stays under the cap without packing 1.5 MiB.

    Dense gabber at 32 kHz q=0 measured ~56 kbps (Bountyhunter), so rate
    is chosen from duration + a conservative q=0 table, not from the
    textbook stereo Vorbis curve.
    """
    budget = budget_kbps(duration_s, HEADROOM)
    target = min(PREF_KBPS, budget)
    reason = f"target {target:.1f} kbps (budget {budget:.1f})"
    rates = usable_rates(source_rate)

    # Duration-first: keep 44.1/32 kHz on short tracks, drop only when
    # the conservative q=0 table says we would miss the budget.
    if duration_s <= 130 and budget >= 70:
        wanted_rate, wanted_q = 44100, 2.0
    elif duration_s <= 185 and budget >= 48:
        wanted_rate, wanted_q = 32000, 2.0
    elif duration_s <= 230 and budget >= 40:
        wanted_rate, wanted_q = 32000, 1.0
    elif duration_s <= 280 and budget >= 34:
        wanted_rate, wanted_q = 22050, 0.5
    elif budget >= 30:
        wanted_rate, wanted_q = 16000, 0.0
    else:
        return cbr_plan(duration_s, source_rate, 0.82, reason + "; cbr")

    rate = _pick_rate(wanted_rate, rates)
    # Drop rate only when the dense-material estimate would miss 90% of the
    # hard cap (not the tighter 85% planning headroom).
    size_budget = budget_kbps(duration_s, 0.90)
    while rate > 16000 and nominal_mono_kbps(max(wanted_q, 0.0), rate) > size_budget:
        idx = list(rates).index(rate)
        if idx + 1 >= len(rates):
            break
        rate = rates[idx + 1]
        reason += f"; drop to {rate}"

    if nominal_mono_kbps(MIN_Q, rate) > size_budget * 1.05:
        return cbr_plan(duration_s, source_rate, 0.82, reason + "; cbr tight")

    q = min(wanted_q, q_for_target(size_budget, rate))
    if q < 0 and wanted_q >= 1.0 and rate >= 32000:
        # Prefer dropping rate over encoding a 32/44.1 kHz file at q=-1.
        idx = list(rates).index(rate) if rate in rates else -1
        if idx >= 0 and idx + 1 < len(rates):
            rate2 = rates[idx + 1]
            q2 = min(wanted_q, q_for_target(size_budget, rate2))
            if q2 >= 0:
                rate, q = rate2, q2
                reason += f"; prefer {rate} Hz q={q}"
    return EncodePlan(
        sample_rate=rate,
        q=q,
        bitrate_kbps=None,
        cutoff_hz=cutoff_for_rate(rate),
        estimated_kbps=nominal_mono_kbps(q, rate),
        reason=reason,
    )


def degrade(plan: EncodePlan, duration_s: float, source_rate: int, overshoot_ratio: float = 1.0) -> EncodePlan | None:
    """Next-worse plan. Big overshoots skip straight to 16 kHz CBR."""
    rates = usable_rates(source_rate)
    if overshoot_ratio >= 1.10 or (plan.bitrate_kbps is not None):
        if plan.bitrate_kbps is not None:
            nxt = max(16, min(int(plan.bitrate_kbps * 0.85), int(budget_kbps(duration_s, 0.80))))
            if nxt >= plan.bitrate_kbps:
                nxt = plan.bitrate_kbps - 2
            if nxt < 16:
                return None
            rate = 16000 if 16000 in rates else rates[-1]
            return EncodePlan(rate, None, nxt, cutoff_for_rate(rate), float(nxt), "cbr step-down")
        return cbr_plan(duration_s, source_rate, 0.82, "cbr after overshoot")

    if plan.q is not None and plan.q > 0:
        new_q = max(MIN_Q, round(plan.q - 1.0, 1))
        return EncodePlan(
            plan.sample_rate, new_q, None, plan.cutoff_hz,
            nominal_mono_kbps(new_q, plan.sample_rate), "lower q",
        )

    idx = list(rates).index(plan.sample_rate) if plan.sample_rate in rates else -1
    if idx >= 0 and idx + 1 < len(rates):
        rate = rates[idx + 1]
        q = min(0.0, q_for_target(budget_kbps(duration_s), rate))
        return EncodePlan(rate, q, None, cutoff_for_rate(rate), nominal_mono_kbps(q, rate), "drop rate")

    return cbr_plan(duration_s, source_rate, 0.82, "cbr fallback")


def plan_baseline(source_rate: int) -> EncodePlan:
    """Old documented baseline: 16 kHz mono libvorbis q=0, default downmix."""
    rate = 16000 if source_rate <= 0 or source_rate >= 16000 else source_rate
    return EncodePlan(rate, 0.0, None, 0, nominal_mono_kbps(0.0, rate), "baseline 16k q=0")


# ---------------------------------------------------------------------------
# Encode
# ---------------------------------------------------------------------------

def encode_vorbis(
    ffmpeg: Path,
    src: Path,
    dst: Path,
    plan: EncodePlan,
    *,
    channels: int,
    better_downmix: bool,
) -> None:
    if dst.exists():
        try:
            dst.unlink()
        except OSError as exc:
            raise YoggError(f"Cannot overwrite {dst}", str(exc)) from exc

    filters: list[str] = []
    if better_downmix and channels >= 2:
        filters.append("pan=mono|c0=0.5*c0+0.5*c1")
    else:
        filters.append("aformat=channel_layouts=mono")
    # Strip sub-sonic rumble so the floor is not spent on DC.
    filters.append("highpass=f=30")

    cmd = [
        str(ffmpeg),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i", str(src),
        "-map", "0:a:0?",
        "-vn",
        "-af", ",".join(filters),
        "-ar", str(plan.sample_rate),
        "-c:a", "libvorbis",
    ]
    if plan.bitrate_kbps is not None:
        cmd += ["-b:a", f"{int(plan.bitrate_kbps)}k"]
    else:
        # Two-token form, q always >= 0. Negative q ("-q:a", "-1") is parsed
        # as a new flag so libvorbis silently stays at default q≈3 — that
        # packed Type-03 to ~2.75 MB. Below q=0 we use managed CBR instead.
        q = max(0.0, float(plan.q))
        cmd += ["-q:a", f"{q:.2f}"]
    if plan.cutoff_hz > 0:
        cmd += ["-cutoff", str(int(plan.cutoff_hz))]
    cmd.append(str(dst))

    proc = run_cmd(cmd)
    if proc.returncode != 0 or not dst.is_file() or dst.stat().st_size == 0:
        raise YoggError(
            "ffmpeg failed to encode OGG (libvorbis).",
            tail(_decode(proc.stderr) or _decode(proc.stdout)),
        )


def encode_baseline_simple(
    ffmpeg: Path,
    src: Path,
    dst: Path,
    sample_rate: int = 16000,
    q: float = 0.0,
) -> None:
    """Naive ffmpeg: -ac 1 -ar 16000 -c:a libvorbis -q:a 0 (no pan/cutoff)."""
    if dst.exists():
        try:
            dst.unlink()
        except OSError:
            pass
    cmd = [
        str(ffmpeg),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i", str(src),
        "-map", "0:a:0?",
        "-vn",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-c:a", "libvorbis",
        "-q:a", f"{max(0.0, q):.2f}",
        str(dst),
    ]
    proc = run_cmd(cmd)
    if proc.returncode != 0 or not dst.is_file() or dst.stat().st_size == 0:
        raise YoggError(
            "ffmpeg baseline encode failed.",
            tail(_decode(proc.stderr) or _decode(proc.stdout)),
        )


def old_q_search(
    ffmpeg: Path,
    src: Path,
    dst: Path,
    *,
    sample_rate: int = 16000,
) -> tuple[int, float, int]:
    """Old always-16 kHz integer q binary search that tries to fill ≤ 1.5 MiB.

    q range is 0..8. Negative q cannot be passed as a separate argv token
    ("-q:a", "-1" is parsed as a new flag). The original script searched
    [-2, 8] and that silent-default is the Type-03 ~2.75 MB failure.
    """
    low, high = 0, 8
    best_q = 0
    best_size = 0
    encodes = 0
    tmp = dst.with_suffix(".ogg.search")
    for _ in range(8):
        mid = (low + high) // 2
        encode_baseline_simple(ffmpeg, src, tmp, sample_rate, float(mid))
        encodes += 1
        size = tmp.stat().st_size
        if size <= LIMIT_BYTES:
            best_q = mid
            best_size = size
            low = mid + 1
            if dst.exists():
                dst.unlink()
            tmp.replace(dst)
        else:
            high = mid - 1
            if best_size <= 0:
                # keep the overflow so the caller can see it
                if dst.exists():
                    dst.unlink()
                tmp.replace(dst)
                best_size = size
                best_q = mid
            elif tmp.exists():
                tmp.unlink()
        if low > high:
            break
    if tmp.exists() and tmp != dst:
        try:
            tmp.unlink()
        except OSError:
            pass
    if not dst.is_file():
        encode_baseline_simple(ffmpeg, src, dst, sample_rate, float(best_q))
        encodes += 1
        best_size = dst.stat().st_size
    return best_size, float(best_q), encodes


# ---------------------------------------------------------------------------
# Public convert
# ---------------------------------------------------------------------------

@dataclass
class EncodeResult:
    output: Path
    src: Path
    duration_s: float
    src_bytes: int
    src_rate: int
    src_channels: int
    out_bytes: int
    sample_rate: int
    mode: str
    q: float | None
    bitrate_kbps: int | None
    cutoff_hz: int
    encodes: int
    used_cbr: bool
    under_cap: bool
    out_kbps: float
    notes: str = ""
    extras: dict = field(default_factory=dict)

    def as_public_dict(self) -> dict:
        d = asdict(self)
        d["output"] = str(self.output)
        d["src"] = str(self.src)
        d["limit_bytes"] = LIMIT_BYTES
        d["under_1_5mb"] = self.under_cap
        return d


def _out_kbps(size: int, duration_s: float) -> float:
    if duration_s <= 0:
        return 0.0
    return size * 8.0 / duration_s / 1000.0


def convert_file(
    src: Path,
    dst: Path,
    *,
    mode: str = "new",
    tools: dict[str, Path] | None = None,
    status: callable | None = None,
) -> EncodeResult:
    """Convert one audio file to mono OGG Vorbis.

    mode:
      new       — adaptive rate / q / cutoff / CBR fallback (default)
      baseline  — ffmpeg -ac 1 -ar 16000 -c:a libvorbis -q:a 0
      old       — 16 kHz integer q binary search filling ≤ 1.5 MiB
    """
    src = src.expanduser().resolve()
    dst = dst.expanduser().resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    log = status or (lambda _m: None)

    tools = tools or require_tools()
    ffmpeg, ffprobe = tools["ffmpeg"], tools["ffprobe"]

    log(f"probe {src.name}")
    info = probe_audio(ffprobe, src)
    src_bytes = src.stat().st_size

    notes: list[str] = []
    encodes = 0
    used_cbr = False
    plan: EncodePlan

    if mode == "baseline":
        log("baseline 16 kHz q=0")
        encode_baseline_simple(ffmpeg, src, dst, 16000, 0.0)
        encodes = 1
        size = dst.stat().st_size
        plan = plan_baseline(info.sample_rate)
        notes.append("baseline: -ac 1 -ar 16000 -c:a libvorbis -q:a 0")
        if size > LIMIT_BYTES:
            notes.append(f"OVERFLOW {size} > {LIMIT_BYTES}")

    elif mode == "old":
        log("old 16 kHz integer q-search")
        size, best_q, encodes = old_q_search(ffmpeg, src, dst)
        plan = EncodePlan(16000, best_q, None, 0, 0.0, "old q-search")
        notes.append(f"old q-search best q={best_q:.0f}, encodes={encodes}")
        if size > LIMIT_BYTES:
            notes.append(f"OVERFLOW {size} > {LIMIT_BYTES}")

    else:
        plan = plan_new(info.duration_s, info.sample_rate)
        last_ok: EncodePlan | None = None
        last_ok_size = 0
        size = 0
        for attempt in range(1, 7):
            log(
                f"encode {attempt}: {plan.sample_rate} Hz {plan.mode_label} "
                f"cutoff={plan.cutoff_hz} ({plan.reason})"
            )
            encode_vorbis(
                ffmpeg, src, dst, plan,
                channels=info.channels,
                better_downmix=True,
            )
            encodes += 1
            size = dst.stat().st_size
            if plan.bitrate_kbps is not None:
                used_cbr = True
            if size <= LIMIT_BYTES:
                last_ok = plan
                last_ok_size = size
                break
            ratio = size / LIMIT_BYTES
            notes.append(
                f"over cap @ {plan.mode_label}/{plan.sample_rate}Hz "
                f"({size} B, {ratio:.2f}x), degrading"
            )
            nxt = degrade(plan, info.duration_s, info.sample_rate, overshoot_ratio=ratio)
            if nxt is None or (
                nxt.sample_rate == plan.sample_rate
                and nxt.q == plan.q
                and nxt.bitrate_kbps == plan.bitrate_kbps
            ):
                # Force a different CBR if degrade stalled.
                forced = cbr_plan(info.duration_s, info.sample_rate, 0.78, "forced cbr")
                if (
                    forced.bitrate_kbps == plan.bitrate_kbps
                    and forced.sample_rate == plan.sample_rate
                ):
                    break
                nxt = forced
            plan = nxt
        if last_ok is not None:
            plan = last_ok
            size = last_ok_size
        if size > LIMIT_BYTES:
            notes.append(f"OVERFLOW {size} > {LIMIT_BYTES} after {encodes} encodes")

    size = dst.stat().st_size if dst.is_file() else 0
    kbps = _out_kbps(size, info.duration_s)
    under = 0 < size <= LIMIT_BYTES
    notes.append(plan.reason)
    return EncodeResult(
        output=dst,
        src=src,
        duration_s=info.duration_s,
        src_bytes=src_bytes,
        src_rate=info.sample_rate,
        src_channels=info.channels,
        out_bytes=size,
        sample_rate=plan.sample_rate,
        mode=plan.mode_label,
        q=plan.q,
        bitrate_kbps=plan.bitrate_kbps,
        cutoff_hz=plan.cutoff_hz,
        encodes=encodes,
        used_cbr=used_cbr,
        under_cap=under,
        out_kbps=round(kbps, 2),
        notes="; ".join(notes),
        extras={"src_codec": info.codec, "src_bitrate": info.bit_rate},
    )


def iter_inputs(path: Path) -> list[Path]:
    path = path.expanduser().resolve()
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise YoggError(f"Not a file or folder: {path}")
    files = [
        p for p in sorted(path.iterdir(), key=lambda x: x.name.lower())
        if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES
    ]
    if not files:
        raise YoggError(f"No audio files in {path}")
    return files


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="yogg",
        description=(
            "Convert MP3 (or a folder) to mono OGG Vorbis, hard-capped at "
            f"{LIMIT_BYTES} bytes (1.5 MiB). No args opens the GUI. "
            f"Author: {AUTHOR}."
        ),
    )
    p.add_argument("input", help="Audio file or folder")
    p.add_argument(
        "-o", "--output",
        help="Output file (single input) or output folder (folder input)",
    )
    p.add_argument(
        "--mode",
        choices=("new", "baseline", "old"),
        default="new",
        help="new=adaptive (default), baseline=16k q=0, old=16k q-search",
    )
    p.add_argument("--json", action="store_true", help="Print JSON result(s)")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    return p.parse_args(argv)


def cli_main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    src = Path(args.input)
    try:
        tools = require_tools()
        inputs = iter_inputs(src)
        out_arg = Path(args.output) if args.output else None
        results: list[EncodeResult] = []

        def status(msg: str) -> None:
            if not args.quiet and not args.json:
                print(msg, flush=True)

        multi = len(inputs) > 1 or src.is_dir()
        if multi:
            out_dir = out_arg if out_arg is not None else (src if src.is_dir() else src.parent)
            if out_arg is not None and out_arg.suffix.lower() == ".ogg" and not out_arg.is_dir():
                raise YoggError("Folder input requires -o to be a directory.")
            out_dir.mkdir(parents=True, exist_ok=True)
        else:
            out_dir = None

        failed = 0
        for item in inputs:
            if multi:
                dst = default_output_path(item, out_dir)
            elif out_arg is not None:
                dst = out_arg
                if dst.suffix.lower() != ".ogg":
                    dst.mkdir(parents=True, exist_ok=True)
                    dst = default_output_path(item, dst)
            else:
                dst = default_output_path(item, None)
            try:
                res = convert_file(item, dst, mode=args.mode, tools=tools, status=status)
                results.append(res)
                flag = "OK" if res.under_cap else "OVER"
                if not args.json:
                    mb = res.out_bytes / (1024 * 1024)
                    print(
                        f"{flag}  {res.output.name}  {mb:.3f} MiB  "
                        f"{res.out_kbps:.1f} kbps  {res.sample_rate} Hz  "
                        f"{res.mode}  ({res.encodes} encode(s))",
                        flush=True,
                    )
            except YoggError as exc:
                failed += 1
                print(f"FAIL  {item.name}: {exc.full()}", file=sys.stderr)

        if args.json:
            payload = [r.as_public_dict() for r in results]
            json.dump(payload if multi else (payload[0] if payload else {}), sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")

        overs = [r for r in results if not r.under_cap]
        if overs:
            return 2
        return 1 if failed else 0
    except YoggError as exc:
        print(exc.full(), file=sys.stderr)
        return 1


def _wants_gui(argv: list[str]) -> bool:
    if "--cli" in argv:
        return False
    if not argv:
        return True
    if argv[0] in ("--gui", "-g"):
        return True
    return "--gui" in argv and not any(
        a for a in argv if a not in ("--gui", "-g") and not a.startswith("-")
    )


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if _wants_gui(argv):
        try:
            from yogg_gui import run_gui
        except ImportError:
            print(
                "GUI requires customtkinter.\n"
                "Install: py -3.12 -m pip install customtkinter\n"
                "CLI:     py -3.12 y_ogg.py file.mp3",
                file=sys.stderr,
            )
            return 1
        return int(run_gui() or 0)
    argv = [a for a in argv if a not in ("--cli", "--gui", "-g")]
    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
