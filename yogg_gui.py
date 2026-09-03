# -*- coding: utf-8 -*-
"""Y-OGG CustomTkinter GUI — URL download + local MP3 → mono OGG.

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
from yogg_download import (
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
COLOR_TAB_OFF = "#2A2A32"


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
        self.geometry("680x680")
        self.minsize(640, 620)
        self.configure(fg_color=COLOR_BG)

        self._tab = "url"
        self._busy = False

        # Convert-tab state (kept alive when switching tabs)
        self.inputs: list[Path] = []
        self.folder_var = tk.StringVar(value=str(default_finished_dir()))
        self.status_var = tk.StringVar(value="Выберите MP3 или папку — и нажмите «Конвертировать».")
        self.files_var = tk.StringVar(value="Файлы не выбраны")

        # URL-tab state
        self.url_var = tk.StringVar(value="")
        self.host_var = tk.StringVar(value=f"Поддерживаются: {SUPPORTED_HINT}")
        self.dl_folder_var = tk.StringVar(value=str(default_downloads_dir()))
        self.dl_status_var = tk.StringVar(value="Вставь ссылку и нажми «Скачать MP3».")
        self.auto_convert_var = tk.BooleanVar(value=False)

        self._build()
        self._apply_icon()
        self._bind_entry_clipboard(self.url_entry)
        self.url_var.trace_add("write", self._on_url_written)
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

        tab_row = ctk.CTkFrame(wrap, fg_color="transparent")
        tab_row.pack(fill="x", pady=(0, 12))
        tab_row.grid_columnconfigure(0, weight=1)
        tab_row.grid_columnconfigure(1, weight=1)

        self.btn_tab_url = ctk.CTkButton(
            tab_row,
            text="Через ссылку",
            height=36,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=lambda: self._switch_tab("url"),
        )
        self.btn_tab_url.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.btn_tab_conv = ctk.CTkButton(
            tab_row,
            text="Конвертировать скачанное",
            height=36,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=lambda: self._switch_tab("convert"),
        )
        self.btn_tab_conv.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self.body = ctk.CTkFrame(wrap, fg_color=COLOR_BG)
        self.body.pack(fill="both", expand=True)

        self.url_panel = ctk.CTkFrame(self.body, fg_color=COLOR_BG)
        self.convert_panel = ctk.CTkFrame(self.body, fg_color=COLOR_BG)

        self._build_url_panel(self.url_panel)
        self._build_convert_panel(self.convert_panel)

        self.url_panel.pack(fill="both", expand=True)
        self._paint_tabs()

        ctk.CTkLabel(
            self,
            text="Сделано de1ze1 (Вадим Угаров) для бесплатного использования",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLOR_AUTHOR,
            anchor="center",
        ).pack(side="bottom", fill="x", pady=(0, 12))

    def _paint_tabs(self) -> None:
        on = dict(fg_color=COLOR_PRIMARY, hover_color=COLOR_HOVER, text_color=COLOR_TEXT)
        off = dict(fg_color=COLOR_TAB_OFF, hover_color="#3A3A44", text_color=COLOR_TEXT)
        if self._tab == "url":
            self.btn_tab_url.configure(**on)
            self.btn_tab_conv.configure(**off)
        else:
            self.btn_tab_url.configure(**off)
            self.btn_tab_conv.configure(**on)

    def _switch_tab(self, name: str) -> None:
        if name == self._tab:
            self._paint_tabs()
            return
        self._tab = name
        if name == "url":
            self.convert_panel.pack_forget()
            self.url_panel.pack(fill="both", expand=True)
        else:
            self.url_panel.pack_forget()
            self.convert_panel.pack(fill="both", expand=True)
        self._paint_tabs()

    # ----- URL tab -----

    def _build_url_panel(self, wrap: ctk.CTkFrame) -> None:
        ctk.CTkLabel(
            wrap,
            text="Скачать аудио по ссылке (MP3)",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLOR_TEXT,
            anchor="w",
            wraplength=620,
            justify="left",
        ).pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            wrap,
            text=f"YouTube, SoundCloud и VK. Другие сайты — тоже через yt-dlp.",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_HINT,
            anchor="w",
            wraplength=620,
            justify="left",
        ).pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            wrap,
            text="Ссылка",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_MUTED,
            anchor="w",
        ).pack(fill="x")

        url_row = ctk.CTkFrame(wrap, fg_color="transparent")
        url_row.pack(fill="x", pady=(4, 0))

        self.url_entry = ctk.CTkEntry(
            url_row,
            textvariable=self.url_var,
            height=34,
            fg_color=COLOR_SURFACE,
            border_color="#2A2A32",
            text_color=COLOR_TEXT,
            placeholder_text="https://www.youtube.com/watch?v=…",
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        ctk.CTkButton(
            url_row,
            text="Вставить",
            width=100,
            height=34,
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_HOVER,
            command=self._paste_url,
        ).pack(side="right")

        ctk.CTkLabel(
            wrap,
            text="Ctrl+V на русской раскладке работает (keycode 86). Либо кнопка «Вставить» / ПКМ.",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_HINT,
            anchor="w",
            wraplength=620,
            justify="left",
        ).pack(fill="x", pady=(4, 4))

        ctk.CTkLabel(
            wrap,
            textvariable=self.host_var,
            font=ctk.CTkFont(size=12),
            text_color=COLOR_GREEN,
            anchor="w",
        ).pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            wrap,
            text="Папка для MP3",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_MUTED,
            anchor="w",
        ).pack(fill="x")

        dl_row = ctk.CTkFrame(wrap, fg_color="transparent")
        dl_row.pack(fill="x", pady=(4, 0))

        self.dl_folder_entry = ctk.CTkEntry(
            dl_row,
            textvariable=self.dl_folder_var,
            height=34,
            fg_color=COLOR_SURFACE,
            border_color="#2A2A32",
            text_color=COLOR_TEXT,
        )
        self.dl_folder_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        ctk.CTkButton(
            dl_row,
            text="Обзор",
            width=100,
            height=34,
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_HOVER,
            command=self._pick_dl_folder,
        ).pack(side="right")

        self.auto_chk = ctk.CTkCheckBox(
            wrap,
            text="Сразу конвертировать в OGG",
            variable=self.auto_convert_var,
            font=ctk.CTkFont(size=13),
            text_color=COLOR_TEXT,
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_HOVER,
            checkmark_color=COLOR_TEXT,
        )
        self.auto_chk.pack(anchor="w", pady=(14, 0))

        ctk.CTkLabel(
            wrap,
            text="По умолчанию выкл — вкладки остаются разными. OGG пишется в папку конвертера.",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_HINT,
            anchor="w",
            wraplength=620,
            justify="left",
        ).pack(fill="x", pady=(4, 8))

        self.dl_progress = ctk.CTkProgressBar(
            wrap,
            mode="determinate",
            height=8,
            progress_color=COLOR_PRIMARY,
            fg_color="#2A2A32",
        )
        self.dl_progress.pack(fill="x", pady=(8, 8))
        self.dl_progress.set(0)

        ctk.CTkLabel(
            wrap,
            textvariable=self.dl_status_var,
            font=ctk.CTkFont(size=12),
            text_color=COLOR_MUTED,
            anchor="w",
            wraplength=620,
            justify="left",
            height=40,
        ).pack(fill="x")

        self.dl_btn = ctk.CTkButton(
            wrap,
            text="Скачать MP3",
            height=42,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_HOVER,
            command=self._start_download,
        )
        self.dl_btn.pack(fill="x", pady=(8, 0))

    # ----- Convert tab (existing encoder UI) -----

    def _build_convert_panel(self, wrap: ctk.CTkFrame) -> None:
        ctk.CTkLabel(
            wrap,
            text="Конвертировать MP3 в mono OGG (≤ 1.5 МиБ)",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLOR_TEXT,
            anchor="w",
            wraplength=620,
            justify="left",
        ).pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            wrap,
            text="Новый энкодер: adaptive libvorbis, частота 44.1/32/22/16 кГц, q ≥ 0, без набивки файла до потолка.",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_HINT,
            anchor="w",
            wraplength=620,
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
            wraplength=620,
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

    # ----- clipboard (incl. Ctrl+V on Russian layout) -----

    def _bind_entry_clipboard(self, entry: ctk.CTkEntry) -> None:
        inner = getattr(entry, "_entry", None)
        target = inner if inner is not None else entry
        target.bind("<Button-3>", lambda e, w=entry: self._show_ctx(e, w))
        target.bind("<Control-KeyPress>", lambda e, w=entry: self._on_ctrl(e, w))
        entry.bind("<Control-KeyPress>", lambda e, w=entry: self._on_ctrl(e, w))

    def _on_ctrl(self, event: tk.Event, entry: ctk.CTkEntry) -> str | None:
        # keycode is stable across layouts: V=86 C=67 X=88 A=65
        code = getattr(event, "keycode", 0)
        if code == 86:
            self._paste_into(entry)
            return "break"
        if code == 67:
            self._copy_from(entry)
            return "break"
        if code == 88:
            self._cut_from(entry)
            return "break"
        if code == 65:
            self._select_all(entry)
            return "break"
        return None

    def _show_ctx(self, event: tk.Event, entry: ctk.CTkEntry) -> None:
        menu = tk.Menu(
            self,
            tearoff=0,
            bg="#1C1C22",
            fg=COLOR_TEXT,
            activebackground=COLOR_PRIMARY,
            activeforeground="white",
        )
        menu.add_command(label="Вырезать", command=lambda: self._cut_from(entry))
        menu.add_command(label="Копировать", command=lambda: self._copy_from(entry))
        menu.add_command(label="Вставить", command=lambda: self._paste_into(entry))
        menu.add_separator()
        menu.add_command(label="Выделить всё", command=lambda: self._select_all(entry))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _entry_widget(self, entry: ctk.CTkEntry) -> tk.Entry:
        inner = getattr(entry, "_entry", None)
        return inner if inner is not None else entry

    def _select_all(self, entry: ctk.CTkEntry) -> None:
        w = self._entry_widget(entry)
        w.select_range(0, "end")
        w.icursor("end")

    def _copy_from(self, entry: ctk.CTkEntry) -> None:
        w = self._entry_widget(entry)
        try:
            text = w.selection_get()
        except tk.TclError:
            text = entry.get()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)

    def _cut_from(self, entry: ctk.CTkEntry) -> None:
        self._copy_from(entry)
        w = self._entry_widget(entry)
        try:
            w.delete("sel.first", "sel.last")
        except tk.TclError:
            entry.delete(0, "end")

    def _paste_into(self, entry: ctk.CTkEntry) -> None:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return
        text = (text or "").strip()
        w = self._entry_widget(entry)
        try:
            w.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        w.insert("insert", text)

    def _paste_url(self) -> None:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            self.dl_status_var.set("Буфер обмена пуст.")
            return
        self.url_var.set((text or "").strip())
        self.dl_status_var.set("Ссылка вставлена.")

    def _on_url_written(self, *_args: object) -> None:
        raw = self.url_var.get().strip()
        if not raw:
            self.host_var.set(f"Поддерживаются: {SUPPORTED_HINT}")
            return
        url = normalize_url(raw)
        if not looks_like_url(url):
            self.host_var.set("Это не похоже на ссылку")
            return
        kind = detect_host(url)
        extra = ""
        if kind == "other":
            extra = " — не из списка, но попробуем"
        self.host_var.set(f"Источник: {host_label(url)}{extra}")

    # ----- convert list helpers -----

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

    def _pick_dl_folder(self) -> None:
        path = filedialog.askdirectory(
            parent=self,
            title="Папка для MP3",
            initialdir=self.dl_folder_var.get() or str(default_downloads_dir()),
        )
        if path:
            self.dl_folder_var.set(path)

    # ----- convert worker -----

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

        self._set_busy(True, "convert")
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

    # ----- download worker -----

    def _ask_playlist_mode(self, url: str) -> str | None:
        """Return 'first' | 'all' | None (cancel)."""
        kind = host_label(url)
        # askyesnocancel: True=first, False=entire playlist, None=cancel
        answer = messagebox.askyesnocancel(
            APP_NAME,
            f"Это похоже на плейлист ({kind}).\n\n"
            "Весь плейлист не качается без подтверждения.\n\n"
            "«Да» — только первый трек\n"
            "«Нет» — скачать ВЕСЬ плейлист (может быть много файлов)\n"
            "«Отмена» — ничего не делать",
            parent=self,
        )
        if answer is True:
            return "first"
        if answer is False:
            return "all"
        return None

    def _start_download(self) -> None:
        if self._busy:
            return
        url = normalize_url(self.url_var.get())
        if not looks_like_url(url):
            messagebox.showwarning(
                APP_NAME,
                "Вставь ссылку на трек (YouTube, SoundCloud, VK).",
                parent=self,
            )
            return
        folder = Path(self.dl_folder_var.get().strip() or str(default_downloads_dir()))
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Не удалось создать папку:\n{exc}", parent=self)
            return
        try:
            require_ytdlp()
            require_tools()
        except YoggError as exc:
            messagebox.showerror(APP_NAME, exc.full(), parent=self)
            return

        playlist_mode = "single"
        if playlist_choice_needed(url):
            choice = self._ask_playlist_mode(url)
            if choice is None:
                return
            playlist_mode = choice

        auto = bool(self.auto_convert_var.get())
        ogg_folder = Path(self.folder_var.get().strip() or str(default_finished_dir()))
        if auto:
            try:
                ogg_folder.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                messagebox.showerror(APP_NAME, f"Не удалось создать папку OGG:\n{exc}", parent=self)
                return

        self._set_busy(True, "download")
        self.dl_progress.set(0)
        self.dl_status_var.set("Запускаю yt-dlp…")
        threading.Thread(
            target=self._download_worker,
            args=(url, folder, playlist_mode, auto, ogg_folder),
            daemon=True,
        ).start()

    def _download_worker(
        self,
        url: str,
        folder: Path,
        playlist_mode: str,
        auto: bool,
        ogg_folder: Path,
    ) -> None:
        def status(msg: str) -> None:
            self.after(0, lambda m=msg: self.dl_status_var.set(m))

        def progress(v: float) -> None:
            self.after(0, lambda x=v: self.dl_progress.set(x))

        try:
            result = download_audio_mp3(
                url,
                folder,
                playlist_mode=playlist_mode,
                status=status,
                progress=progress,
            )
        except YoggError as exc:
            self.after(0, lambda e=exc: self._on_dl_fail(e.full()))
            return
        except Exception:
            self.after(0, lambda t=traceback.format_exc(): self._on_dl_fail(
                "Неожиданная ошибка.\n\n" + t[-1800:]
            ))
            return

        ogg_note = ""
        ogg_err = ""
        if auto:
            try:
                status("Конвертирую скачанный MP3 в OGG…")
                tools = require_tools()
                dst = default_output_path(result.path, ogg_folder)
                res = convert_file(
                    result.path,
                    dst,
                    mode="new",
                    tools=tools,
                    status=lambda m: status(_status_ru(m)),
                )
                flag = "OK" if res.under_cap else "OVER"
                ogg_note = (
                    f"\n\nOGG: {res.output}\n"
                    f"{_fmt_size(res.out_bytes)}  ·  {flag} {res.mode} {res.sample_rate} Гц"
                )
            except YoggError as exc:
                ogg_err = exc.full()
            except Exception:
                ogg_err = traceback.format_exc()[-1200:]

        self.after(
            0,
            lambda r=result, n=ogg_note, e=ogg_err: self._on_dl_done(r, n, e),
        )

    def _on_dl_done(self, result, ogg_note: str, ogg_err: str) -> None:
        self._stop_busy()
        self.dl_progress.set(1.0)
        size = result.path.stat().st_size if result.path.is_file() else 0
        self.dl_status_var.set(f"Готово: {result.path.name}  ·  {_fmt_size(size)}")
        # Offer the file on the convert tab without destroying previous picks.
        try:
            seen = {str(p).lower() for p in self.inputs}
            if str(result.path).lower() not in seen:
                self.inputs.append(result.path)
                self._refresh_list()
        except Exception:
            pass
        cookies_line = ""
        if result.used_cookies:
            cookies_line = "\nCookies: cookies.txt рядом с программой."
        text = (
            f"MP3 сохранён:\n{result.path}\n\n"
            f"{_fmt_size(size)}\n"
            f"Источник: {HOST_SAFE.get(result.host, result.host)}"
            f"{cookies_line}{ogg_note}"
        )
        if ogg_err:
            text += f"\n\nMP3 скачан, но конвертация в OGG не удалась:\n{ogg_err}"
            messagebox.showwarning(APP_NAME, text, parent=self)
        else:
            messagebox.showinfo(APP_NAME, text, parent=self)

    def _on_dl_fail(self, text: str) -> None:
        self._stop_busy()
        self.dl_status_var.set("Ошибка. Подробности в окне сообщения.")
        messagebox.showerror(APP_NAME, text, parent=self)

    def _on_fail(self, text: str) -> None:
        self._stop_busy()
        self._set_status("Ошибка. Подробности в окне сообщения.")
        messagebox.showerror(APP_NAME, text, parent=self)

    def _set_busy(self, busy: bool, who: str) -> None:
        self._busy = busy
        try:
            if busy:
                self.start_btn.configure(state="disabled")
                self.dl_btn.configure(state="disabled", text="Скачиваю…" if who == "download" else "Скачать MP3")
                if who == "convert":
                    self.start_btn.configure(text="Обработка…")
            else:
                self._stop_busy()
        except tk.TclError:
            pass

    def _stop_busy(self) -> None:
        self._busy = False
        try:
            self.start_btn.configure(state="normal", text="Конвертировать")
            self.dl_btn.configure(state="normal", text="Скачать MP3")
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


HOST_SAFE = {
    "youtube": "YouTube",
    "soundcloud": "SoundCloud",
    "vk": "VK (ВКонтакте)",
    "other": "yt-dlp",
}


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


if __name__ == "__main__":
    sys.exit(run_gui())
