# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir spec for the Y-OGG Windows GUI.

ffmpeg/ffprobe are NOT bundled here — copy them into the product bin\\
after the build (they must not be committed to git).
"""

from PyInstaller.building.build_main import COLLECT, EXE, PYZ, Analysis
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = ["customtkinter", "yogg_gui"]

tmp_ret = collect_all("customtkinter")
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

datas += [("icon.ico", ".")]

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
