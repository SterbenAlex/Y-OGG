# -*- coding: utf-8 -*-
"""Y-OGG CustomTkinter GUI — local MP3/folder → mono OGG via the new encoder.

Author: de1ze1 (Вадим Угаров). Free to use.
Does not reimplement encoding: every convert goes through y_ogg.convert_file
(mode="new"). Negative libvorbis q is never passed to ffmpeg.
"""

from __future__ import annotations

import sys
import threading
import traceback
from pathlib import Path

try:
    import customtkinter as ctk
except ImportError:
    ctk = None  # type: ignore

import tkinter as tk
from tkinter import filedialog, messagebox

from y_ogg import (
    APP_NAME,
    APP_VERSION,
    LIMIT_BYTES,
    YoggError,
    convert_file,
    default_finished_dir,
    default_output_path,
    iter_inputs,
    require_tools,
    yogg_root,
)

COLOR_BG = "#121214"
COLOR_SURFACE = "#1C1C22"
COLOR_PRIMARY = "#7B2CBF"
COLOR_HOVER = "#9D4EDD"
COLOR_AUTHOR = "#D35400"
COLOR_GREEN = "#3DDC84"
COLOR_TEXT = "#F2F0F5"
COLOR_MUTED = "#9A96A8"
COLOR_HINT = "#7A7688"
COLOR_ERR = "#E74C3C"


def _icon_path() -> Path | None:
    here = yogg_root()
    candidates = [
        here / "icon.ico",
        here / "_internal" / "icon.ico",
    ]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "icon.ico")
    for p in candidates:
        if p.is_file():
            return p
    return None


def _status_ru(msg: str) -> str:
    if msg.startswith("probe "):
        return f"Читаю {msg[6:].strip()}…"
    if msg.startswith("encode "):
        return f"Кодирую {msg[7:].strip()}…"
    if msg.startswith("baseline"):
        return "Базовый режим 16 кГц q=0…"
    if msg.startswith("old "):
        return "Старый 16 кГц q-search…"
    return msg


def _fmt_size(n: int) -> str:
    mb = n / (1024 * 1024)
    return f"{mb:.3f} МиБ ({n} байт)"


class YOggApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("660x600")
        self.minsize(640, 560)
        self.configure(fg_color=COLOR_BG)

        self.inputs: list[Path] = []
        self._busy = False

        self.folder_var = tk.StringVar(value=str(default_finished_dir()))
        self.status_var = tk.StringVar(value="Выберите MP3 или папку — и нажмите «Конвертировать».")
        self.files_var = tk.StringVar(value="Файлы не выбраны")

        self._build()
        self._apply_icon()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _apply_icon(self) -> None:
        icon = _icon_path()
        if icon is None:
            return
        try:
            self.iconbitmap(str(icon))
        except Exception:
            pass

    def _build(self) -> None:
        pad_x = 18
        wrap = ctk.CTkFrame(self, fg_color=COLOR_BG)
        wrap.pack(fill="both", expand=True, padx=pad_x, pady=(14, 6))

        ctk.CTkLabel(
            wrap,
            text="Конвертировать MP3 в mono OGG (≤ 1.5 МиБ)",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLOR_TEXT,
            anchor="w",
            wraplength=610,
            justify="left",
        ).pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            wrap,
            text="Новый энкодер: adaptive libvorbis, частота 44.1/32/22/16 кГц, q ≥ 0, без набивки файла до потолка.",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_HINT,
            anchor="w",
            wraplength=610,
            justify="left",
        ).pack(fill="x", pady=(0, 12))

        btn_row = ctk.CTkFrame(wrap, fg_color="transparent")
        btn_row.pack(fill="x")

        ctk.CTkButton(
            btn_row,
            text="Выбрать MP3",
            width=150,
            height=34,
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_HOVER,
            command=self._pick_files,
        ).pack(side="left")

        ctk.CTkButton(
            btn_row,
            text="Выбрать папку",
            width=150,
            height=34,
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_HOVER,
            command=self._pick_input_folder,
        ).pack(side="left", padx=(8, 0))

        ctk.CTkButton(
            btn_row,
            text="Очистить",
            width=100,
            height=34,
            fg_color="#2A2A32",
            hover_color="#3A3A44",
            text_color=COLOR_TEXT,
            command=self._clear_inputs,
        ).pack(side="right")

        ctk.CTkLabel(
            wrap,
            textvariable=self.files_var,
            font=ctk.CTkFont(size=12),
            text_color=COLOR_GREEN,
            anchor="w",
        ).pack(fill="x", pady=(8, 4))

        self.list_box = ctk.CTkTextbox(
            wrap,
            height=110,
            fg_color=COLOR_SURFACE,
            text_color=COLOR_TEXT,
            border_color="#2A2A32",
            border_width=1,
            wrap="none",
            font=ctk.CTkFont(size=12),
        )
        self.list_box.pack(fill="both", expand=True, pady=(0, 8))
        self.list_box.configure(state="disabled")

        ctk.CTkLabel(
            wrap,
            text="Папка сохранения",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_MUTED,
            anchor="w",
        ).pack(fill="x")

        folder_row = ctk.CTkFrame(wrap, fg_color="transparent")
        folder_row.pack(fill="x", pady=(4, 0))

        self.folder_entry = ctk.CTkEntry(
            folder_row,
            textvariable=self.folder_var,
            height=34,
            fg_color=COLOR_SURFACE,
            border_color="#2A2A32",
            text_color=COLOR_TEXT,
        )
        self.folder_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        ctk.CTkButton(
            folder_row,
            text="Обзор",
            width=100,
            height=34,
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_HOVER,
            command=self._pick_out_folder,
        ).pack(side="right")

        self.progress = ctk.CTkProgressBar(
            wrap,
            mode="determinate",
            height=8,
            progress_color=COLOR_PRIMARY,
            fg_color="#2A2A32",
        )
        self.progress.pack(fill="x", pady=(16, 8))
        self.progress.set(0)

        ctk.CTkLabel(
            wrap,
            textvariable=self.status_var,
            font=ctk.CTkFont(size=12),
            text_color=COLOR_MUTED,
            anchor="w",
            wraplength=610,
            justify="left",
            height=40,
        ).pack(fill="x")

        self.start_btn = ctk.CTkButton(
            wrap,
            text="Конвертировать",
            height=42,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_HOVER,
            command=self._start,
        )
        self.start_btn.pack(fill="x", pady=(8, 0))

        ctk.CTkLabel(
            self,
            text="Сделано de1ze1 (Вадим Угаров) для бесплатного использования",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLOR_AUTHOR,
            anchor="center",
        ).pack(side="bottom", fill="x", pady=(0, 12))

    def _set_list_text(self, text: str) -> None:
        self.list_box.configure(state="normal")
        self.list_box.delete("1.0", "end")
        if text:
            self.list_box.insert("1.0", text)
        self.list_box.configure(state="disabled")

    def _refresh_list(self) -> None:
        n = len(self.inputs)
        if n == 0:
            self.files_var.set("Файлы не выбраны")
            self._set_list_text("")
            return
        self.files_var.set(f"Выбрано файлов: {n}")
        self._set_list_text("\n".join(p.name for p in self.inputs))

    def _clear_inputs(self) -> None:
        self.inputs = []
        self._refresh_list()
        self._set_status("Список очищен.")

    def _pick_files(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self,
            title="Выбрать MP3 (можно несколько)",
            filetypes=[
                ("MP3", "*.mp3"),
                ("Аудио", "*.mp3 *.wav *.flac *.m4a *.aac *.ogg *.wma *.aiff *.aif"),
                ("Все файлы", "*.*"),
            ],
        )
        if not paths:
            return
        added = [Path(p) for p in paths]
        seen = {str(p).lower() for p in self.inputs}
        for p in added:
            if str(p).lower() not in seen:
                self.inputs.append(p)
                seen.add(str(p).lower())
        self._refresh_list()
        self._set_status(f"Добавлено. Всего файлов: {len(self.inputs)}")

    def _pick_input_folder(self) -> None:
        path = filedialog.askdirectory(parent=self, title="Папка с MP3")
        if not path:
            return
        try:
            files = iter_inputs(Path(path))
        except YoggError as exc:
            messagebox.showwarning(APP_NAME, exc.message, parent=self)
            return
        self.inputs = files
        self._refresh_list()
        self._set_status(f"Папка: {path}  ·  {len(files)} файл(ов)")

    def _pick_out_folder(self) -> None:
        path = filedialog.askdirectory(
            parent=self,
            title="Папка сохранения",
            initialdir=self.folder_var.get() or str(default_finished_dir()),
        )
        if path:
            self.folder_var.set(path)

    def _start(self) -> None:
        if self._busy:
            return
        if not self.inputs:
            messagebox.showwarning(
                APP_NAME,
                "Выберите MP3 или папку с аудиофайлами.",
                parent=self,
            )
            return
        folder = Path(self.folder_var.get().strip() or str(default_finished_dir()))
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Не удалось создать папку:\n{exc}", parent=self)
            return
        try:
            require_tools()
        except YoggError as exc:
            messagebox.showerror(APP_NAME, exc.full(), parent=self)
            return

        self._busy = True
        self.start_btn.configure(state="disabled", text="Обработка…")
        self.progress.set(0)
        self._set_status("Запускаю…")
        items = list(self.inputs)
        threading.Thread(target=self._worker, args=(items, folder), daemon=True).start()

    def _worker(self, items: list[Path], folder: Path) -> None:
        ok: list[tuple[str, int, str]] = []
        failed: list[tuple[str, str]] = []
        total = len(items)
        try:
            tools = require_tools()
        except YoggError as exc:
            self.after(0, lambda e=exc: self._on_fail(e.full()))
            return
        except Exception:
            self.after(0, lambda t=traceback.format_exc(): self._on_fail(
                "Неожиданная ошибка.\n\n" + t[-1800:]
            ))
            return

        for i, src in enumerate(items, start=1):
            self._set_status_threadsafe(f"{i}/{total}: {src.name}")
            self.after(0, lambda v=i / total: self.progress.set(v))
            dst = default_output_path(src, folder)
            try:
                res = convert_file(
                    src,
                    dst,
                    mode="new",
                    tools=tools,
                    status=lambda m: self._set_status_threadsafe(_status_ru(m)),
                )
                flag = "OK" if res.under_cap else "OVER"
                ok.append((res.output.name, res.out_bytes, f"{flag} {res.mode} {res.sample_rate} Гц"))
            except YoggError as exc:
                failed.append((src.name, exc.full()))
            except Exception:
                failed.append((src.name, traceback.format_exc()[-1200:]))

        self.after(0, lambda: self._on_done(ok, failed, folder))

    def _on_done(
        self,
        ok: list[tuple[str, int, str]],
        failed: list[tuple[str, str]],
        folder: Path,
    ) -> None:
        self._stop_busy()
        self.progress.set(1.0 if ok and not failed else self.progress.get())
        lines: list[str] = []
        over = [item for item in ok if item[2].startswith("OVER")]
        if ok:
            last = ok[-1]
            self._set_status(
                f"Готово: {len(ok)} файл(ов) → {folder}  ·  "
                f"последний {last[0]}  {_fmt_size(last[1])}"
            )
            for name, size, meta in ok:
                lines.append(f"• {name}\n  {_fmt_size(size)}  ·  {meta}")
        else:
            self._set_status("Ни один файл не сконвертирован.")
        if failed:
            lines.append("")
            lines.append("Ошибки:")
            for name, err in failed:
                lines.append(f"• {name}: {err.splitlines()[0]}")

        body = "\n".join(lines) if lines else "Нет результатов."
        cap_note = ""
        if over:
            cap_note = f"\n\nВнимание: {len(over)} файл(ов) вышли за 1.5 МиБ."
        title_ok = not failed and not over
        text = (
            f"Сохранено в:\n{folder}\n\n{body}{cap_note}\n\n"
            f"Лимит: {_fmt_size(LIMIT_BYTES)}."
        )
        if title_ok:
            messagebox.showinfo(APP_NAME, text, parent=self)
        elif failed and not ok:
            messagebox.showerror(APP_NAME, text, parent=self)
        else:
            messagebox.showwarning(APP_NAME, text, parent=self)

    def _on_fail(self, text: str) -> None:
        self._stop_busy()
        self._set_status("Ошибка. Подробности в окне сообщения.")
        messagebox.showerror(APP_NAME, text, parent=self)

    def _stop_busy(self) -> None:
        self._busy = False
        try:
            self.start_btn.configure(state="normal", text="Конвертировать")
        except tk.TclError:
            pass

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _set_status_threadsafe(self, text: str) -> None:
        self.after(0, lambda t=text: self._set_status(t))

    def _on_close(self) -> None:
        if self._busy:
            if not messagebox.askokcancel(
                APP_NAME,
                "Идёт обработка. Закрыть окно?",
                parent=self,
            ):
                return
        self.destroy()


def _windows_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            from ctypes import windll
            windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def run_gui() -> int:
    if ctk is None:
        sys.stderr.write(
            "Не найден customtkinter.\n"
            "Установите: py -3.12 -m pip install customtkinter\n"
            "CLI без GUI: py -3.12 y_ogg.py file.mp3\n"
        )
        return 1
    _windows_dpi()
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    app = YOggApp()
    app.mainloop()
    return 0
