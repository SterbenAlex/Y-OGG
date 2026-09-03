# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir spec for the Y-OGG Windows GUI.

ffmpeg/ffprobe are NOT bundled here — copy them into the product bin\\
after the build (they must not be committed to git).

If bin/yt-dlp.exe is present at build time it is added as
--add-binary bin/yt-dlp.exe;bin so the frozen app can download from URLs.
The exe itself is gitignored; copying into dist\\Y-OGG\\bin\\ after the
build is still the documented product step.
"""

from pathlib import Path

from PyInstaller.building.build_main import COLLECT, EXE, PYZ, Analysis
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = ["customtkinter", "yogg_gui", "yogg_download"]

tmp_ret = collect_all("customtkinter")
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

datas += [("icon.ico", ".")]

_yt = Path("bin") / "yt-dlp.exe"
if _yt.is_file():
    binaries += [(str(_yt), "bin")]

a = Analysis(
    ["y_ogg.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Y-OGG",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon="icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Y-OGG",
)
