"""URP Map Converter: pack and unpack texture maps by channel.

A tkinter window; files can be dragged straight from Explorer.
Pixel work lives in core.py, channel layouts and name parsing in presets.py.
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk

import core
import presets
from presets import (
    BASECOLOR_PRESET,
    MASK_PRESETS,
    PRESETS,
    PRESET_BY_NAME,
    ROLE_CONST,
    ROLE_SUFFIX,
    ROLE_TITLES,
)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    BASE_TK = TkinterDnD.Tk
    HAS_DND = True
except Exception:  # library missing: run without drag and drop
    BASE_TK = tk.Tk
    HAS_DND = False

APP_TITLE = "URP Map Converter"
LETTERS = (("R", "#c0392b"), ("G", "#1e8449"), ("B", "#2a6fd6"), ("A", "#4d4d4d"))
CHANNEL_LABELS = ("Авто", "R", "G", "B", "A")
CHANNEL_VALUES = ("auto", "r", "g", "b", "a")
SIZE_LABELS = ("По самой большой", "По самой маленькой", "512", "1024", "2048", "4096", "8192")
SIZE_VALUES = ("max", "min", "512", "1024", "2048", "4096", "8192")
BITS_LABELS = ("8 бит", "16 бит")
FILE_TYPES = [("Изображения", "*.png *.tga *.tif *.tiff *.jpg *.jpeg *.bmp *.webp"), ("Все файлы", "*.*")]
EMPTY_ROW_TEXT = "перетащи файл сюда"


def register_drop(widget, callback) -> None:
    """Accept files dropped from Explorer on this widget."""
    if not HAS_DND:
        return
    try:
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<Drop>>", lambda e: callback(parse_drop(widget, e.data)))
    except Exception:
        pass


def parse_drop(widget, data) -> list[Path]:
    try:
        items = widget.tk.splitlist(data)
    except Exception:
        items = [data]
    return [Path(str(p)) for p in items]


def collect_images(paths) -> list[Path]:
    """Expand dropped folders into a flat list of images."""
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            out += sorted(f for f in p.rglob("*") if f.is_file() and core.is_image(f))
        elif p.is_file() and core.is_image(p):
            out.append(p)
    return out


def to_photo(plane_or_rgb: np.ndarray, box: int) -> ImageTk.PhotoImage:
    """Turn a plane or an RGB array into a thumbnail for the window."""
    data = np.clip(np.rint(plane_or_rgb * 255), 0, 255).astype(np.uint8)
    mode = "L" if data.ndim == 2 else "RGB"
    img = Image.fromarray(data, mode=mode)
    img.thumbnail((box, box), Image.NEAREST)
    return ImageTk.PhotoImage(img)


def same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


class SlotRow(ttk.Frame):
    """One output channel: source file, source channel, invert, fill value."""

    def __init__(self, master, index: int, on_change, on_file=None):
        super().__init__(master, padding=(0, 3))
        self.index = index
        self.on_change = on_change
        self.on_file = on_file
        self.slot = core.Slot()
        self.role = "none"

        letter, color = LETTERS[index]
        tk.Label(self, text=letter, fg=color, width=2, font=("Segoe UI", 12, "bold")).grid(row=0, column=0)

        self.role_var = tk.StringVar(value=ROLE_TITLES["none"])
        ttk.Label(self, textvariable=self.role_var, width=13).grid(row=0, column=1, sticky="w")

        self.file_var = tk.StringVar(value=EMPTY_ROW_TEXT)
        self.drop = tk.Label(
            self,
            textvariable=self.file_var,
            anchor="w",
            relief="groove",
            padx=8,
            pady=5,
            bg="#f4f4f4",
            fg="#777777",
        )
        self.drop.grid(row=0, column=2, sticky="ew", padx=(0, 6))
        self.columnconfigure(2, weight=1)
        self.drop.bind("<Button-1>", lambda e: self.browse())
        register_drop(self.drop, self._on_drop)

        self.channel_var = tk.StringVar(value=CHANNEL_LABELS[0])
        box = ttk.Combobox(self, textvariable=self.channel_var, values=CHANNEL_LABELS, width=5, state="readonly")
        box.grid(row=0, column=3, padx=(0, 6))
        box.bind("<<ComboboxSelected>>", lambda e: self._apply())

        self.invert_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self, text="инверт", variable=self.invert_var, command=self._apply).grid(row=0, column=4)

        self.const_var = tk.StringVar(value="0.00")
        self.const_box = ttk.Spinbox(
            self,
            textvariable=self.const_var,
            from_=0.0,
            to=1.0,
            increment=0.05,
            width=5,
            format="%.2f",
            command=self._apply,
        )
        self.const_box.grid(row=0, column=5, padx=(8, 0))
        self.const_box.bind("<FocusOut>", lambda e: self._apply())
        self.const_box.bind("<Return>", lambda e: self._apply())

        ttk.Button(self, text="x", width=3, command=self.clear).grid(row=0, column=6, padx=(6, 0))

    # --- state ---

    def set_role(self, role: str) -> None:
        self.role = role
        self.role_var.set(ROLE_TITLES[role])
        # base colour rows read the channel of their own letter, the rest start at Auto
        self.channel_var.set(LETTERS[self.index][0] if role == "basecolor" else CHANNEL_LABELS[0])
        if not self.slot.filled:
            self.const_var.set(f"{ROLE_CONST[role]:.2f}")
        self._apply()

    def set_file(self, path: Path, notify: bool = True) -> None:
        try:
            info = core.describe(path)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Не читается файл:\n{path}\n\n{exc}")
            return
        self.slot.path = path
        self.file_var.set(f"{path.name}   {info}")
        self.drop.configure(fg="#222222", bg="#eef5ff")
        # roughness dropped into a smoothness row (or the other way round) needs inverting
        _, role = presets.split_name(path.stem)
        if {role, self.role} == {"smoothness", "roughness"}:
            self.invert_var.set(True)
        self._apply()
        if notify and self.on_file:
            self.on_file(self, path)

    def clear(self, notify: bool = True) -> None:
        self.slot.path = None
        self.invert_var.set(False)
        self.file_var.set(EMPTY_ROW_TEXT)
        self.drop.configure(fg="#777777", bg="#f4f4f4")
        self.const_var.set(f"{ROLE_CONST[self.role]:.2f}")
        self._apply()
        if notify and self.on_file:
            self.on_file(self, None)

    def browse(self) -> None:
        name = filedialog.askopenfilename(title=f"Карта для канала {LETTERS[self.index][0]}", filetypes=FILE_TYPES)
        if name:
            self.set_file(Path(name))

    def _on_drop(self, paths) -> None:
        files = collect_images(paths)
        if files:
            self.set_file(files[0])

    def _apply(self) -> None:
        self.slot.channel = CHANNEL_VALUES[CHANNEL_LABELS.index(self.channel_var.get())]
        self.slot.invert = self.invert_var.get()
        try:
            self.slot.constant = max(0.0, min(1.0, float(self.const_var.get().replace(",", "."))))
        except ValueError:
            self.slot.constant = 0.0
        self.const_box.state(["disabled"] if self.slot.filled else ["!disabled"])
        self.on_change()


class PackTab(ttk.Frame):
    """Pack up to four maps into one texture."""

    def __init__(self, master):
        super().__init__(master, padding=12)
        self.preview_ids = []
        self._pending = None

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="Раскладка:").pack(side="left")
        self.preset_var = tk.StringVar(value=PRESETS[0].name)
        combo = ttk.Combobox(top, textvariable=self.preset_var, values=[p.name for p in PRESETS], state="readonly", width=34)
        combo.pack(side="left", padx=8)
        combo.bind("<<ComboboxSelected>>", lambda e: self.apply_preset())

        self.hint = ttk.Label(self, text="", wraplength=900, foreground="#555555")
        self.hint.pack(fill="x", pady=(6, 10))

        rows = ttk.LabelFrame(self, text="Каналы результата", padding=8)
        rows.pack(fill="x")
        head = ttk.Frame(rows)
        head.pack(fill="x")
        ttk.Label(head, text="канал      карта", foreground="#888888").pack(side="left")
        ttk.Label(head, text="файл · откуда брать · инверсия · заливка", foreground="#888888").pack(side="right")
        self.rows = []
        for i in range(4):
            row = SlotRow(rows, i, self.schedule_preview, self.on_row_file)
            row.pack(fill="x")
            self.rows.append(row)

        opts = ttk.Frame(self)
        opts.pack(fill="x", pady=12)
        ttk.Label(opts, text="Разрешение:").pack(side="left")
        self.size_var = tk.StringVar(value=SIZE_LABELS[0])
        ttk.Combobox(opts, textvariable=self.size_var, values=SIZE_LABELS, state="readonly", width=18).pack(side="left", padx=(6, 16))
        ttk.Label(opts, text="Глубина:").pack(side="left")
        self.bits_var = tk.StringVar(value=BITS_LABELS[0])
        ttk.Combobox(opts, textvariable=self.bits_var, values=BITS_LABELS, state="readonly", width=8).pack(side="left", padx=(6, 16))
        self.rgb_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="без альфы (RGB)", variable=self.rgb_var).pack(side="left")

        bottom = ttk.Frame(self)
        bottom.pack(fill="both", expand=True)

        prev = ttk.LabelFrame(bottom, text="Превью", padding=8)
        prev.pack(side="left", anchor="n")
        self.prev_rgb = tk.Label(prev, width=20, height=9, bg="#e8e8e8")
        self.prev_rgb.grid(row=0, column=0, padx=4)
        self.prev_a = tk.Label(prev, width=20, height=9, bg="#e8e8e8")
        self.prev_a.grid(row=0, column=1, padx=4)
        ttk.Label(prev, text="RGB").grid(row=1, column=0)
        ttk.Label(prev, text="альфа").grid(row=1, column=1)

        actions = ttk.Frame(bottom, padding=(16, 8))
        actions.pack(side="left", anchor="n", fill="x", expand=True)
        ttk.Button(actions, text="Сохранить текстуру...", command=self.save).pack(fill="x")
        ttk.Button(actions, text="Очистить всё", command=self.reset).pack(fill="x", pady=6)
        self.status = ttk.Label(actions, text="", wraplength=380, foreground="#2a6fd6")
        self.status.pack(fill="x", pady=(8, 0))

        register_drop(self, self.drop_many)
        self.apply_preset()

    @property
    def preset(self):
        return PRESET_BY_NAME[self.preset_var.get()]

    def apply_preset(self) -> None:
        preset = self.preset
        self.hint.configure(text=preset.hint)
        self.rgb_var.set(preset.drop_alpha)
        for row, role in zip(self.rows, preset.roles):
            row.set_role(role)

    def on_row_file(self, row: SlotRow, path: Path | None) -> None:
        """A base colour file set on one row fills every base colour row."""
        if row.role != "basecolor":
            return
        for other in self.rows:
            if other is row or other.role != "basecolor":
                continue
            if path is None:
                other.clear(notify=False)
            else:
                other.set_file(path, notify=False)

    def drop_many(self, paths) -> None:
        """Files dropped on the window: sort them into rows by file name."""
        files = collect_images(paths)
        if not files:
            return
        found: dict[str, Path] = {}
        for f in files:
            _, role = presets.split_name(f.stem)
            if role:
                found.setdefault(role, f)
        used = False
        for row in self.rows:
            path, invert = presets.pick_for_role(found, row.role)
            if path:
                row.set_file(path, notify=False)
                row.invert_var.set(invert)
                row._apply()
                used = True
        if used:
            self.status.configure(text="")
            return
        # the names told us nothing: a single file goes to the first empty row
        if len(files) == 1:
            target = next((r for r in self.rows if not r.slot.filled), self.rows[0])
            target.set_file(files[0])
            return
        self.status.configure(text="По именам файлов роли не угадались, разложи вручную.")

    def reset(self) -> None:
        for row in self.rows:
            row.clear(notify=False)
        self.status.configure(text="")

    def schedule_preview(self) -> None:
        if self._pending:
            self.after_cancel(self._pending)
        self._pending = self.after(120, self.update_preview)

    def update_preview(self) -> None:
        self._pending = None
        slots = [r.slot for r in self.rows]
        try:
            arr = core.pack(slots, (160, 160))
        except Exception as exc:
            self.status.configure(text=f"Превью не собралось: {exc}")
            return
        self.preview_ids = [to_photo(arr[..., :3], 160), to_photo(arr[..., 3], 160)]
        self.prev_rgb.configure(image=self.preview_ids[0], width=160, height=160)
        self.prev_a.configure(image=self.preview_ids[1], width=160, height=160)

    def save(self) -> None:
        slots = [r.slot for r in self.rows]
        if not any(s.filled for s in slots):
            messagebox.showinfo(APP_TITLE, "Сначала положи хотя бы одну карту.")
            return
        first = next(s.path for s in slots if s.filled)
        base, _ = presets.split_name(first.stem)
        name = filedialog.asksaveasfilename(
            title="Куда сохранить",
            initialdir=str(first.parent),
            initialfile=f"{base}{self.preset.out_suffix}.png",
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("TGA", "*.tga"), ("TIFF", "*.tif")],
        )
        if not name:
            return
        target = Path(name)
        if any(same_file(target, s.path) for s in slots if s.filled):
            messagebox.showerror(APP_TITLE, "Это один из исходников. Выбери другое имя, чтобы его не затереть.")
            return
        bits = 16 if self.bits_var.get().startswith("16") else 8
        mode = SIZE_VALUES[SIZE_LABELS.index(self.size_var.get())]
        size = core.target_size(slots, mode)
        self.status.configure(text="Считаю...")
        self.update_idletasks()
        try:
            arr = core.pack(slots, size)
            core.save(target, arr, bits=bits, drop_alpha=self.rgb_var.get())
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Не сохранилось:\n{exc}")
            self.status.configure(text="")
            return
        self.status.configure(text=f"Готово: {target.name}  {size[0]}x{size[1]}, {bits} бит")


class UnpackTab(ttk.Frame):
    """Split a packed texture back into separate maps."""

    def __init__(self, master):
        super().__init__(master, padding=12)
        self.path: Path | None = None
        self.planes: list[np.ndarray] = []
        self.thumbs: list[ImageTk.PhotoImage] = []
        self.roles: tuple[str, ...] = PRESETS[0].roles

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="Раскладка:").pack(side="left")
        self.preset_var = tk.StringVar(value=PRESETS[0].name)
        combo = ttk.Combobox(top, textvariable=self.preset_var, values=[p.name for p in PRESETS], state="readonly", width=34)
        combo.pack(side="left", padx=8)
        combo.bind("<<ComboboxSelected>>", lambda e: self.apply_preset())

        self.drop_var = tk.StringVar(value="перетащи сюда упакованную текстуру")
        self.drop = tk.Label(self, textvariable=self.drop_var, relief="groove", bg="#f4f4f4", fg="#777777", pady=22)
        self.drop.pack(fill="x", pady=10)
        self.drop.bind("<Button-1>", lambda e: self.browse())
        register_drop(self.drop, self._on_drop)
        register_drop(self, self._on_drop)

        table = ttk.LabelFrame(self, text="Что сохранить", padding=8)
        table.pack(fill="x")
        self.enabled, self.suffix, self.invert, self.previews = [], [], [], []
        for i in range(4):
            letter, color = LETTERS[i]
            tk.Label(table, text=letter, fg=color, width=2, font=("Segoe UI", 12, "bold")).grid(row=0, column=i * 4, pady=4)
            en = tk.BooleanVar(value=True)
            ttk.Checkbutton(table, variable=en).grid(row=0, column=i * 4 + 1)
            sfx = tk.StringVar(value="_Channel")
            ttk.Entry(table, textvariable=sfx, width=14).grid(row=0, column=i * 4 + 2, padx=(0, 4))
            inv = tk.BooleanVar(value=False)
            ttk.Checkbutton(table, text="инверт", variable=inv).grid(row=0, column=i * 4 + 3, padx=(0, 14))
            thumb = tk.Label(table, width=13, height=6, bg="#e8e8e8")
            thumb.grid(row=1, column=i * 4, columnspan=4, pady=6)
            self.enabled.append(en)
            self.suffix.append(sfx)
            self.invert.append(inv)
            self.previews.append(thumb)

        self.hint = ttk.Label(self, text="", foreground="#555555", wraplength=900)
        self.hint.pack(fill="x", pady=(6, 0))

        opts = ttk.Frame(self)
        opts.pack(fill="x", pady=10)
        ttk.Label(opts, text="Глубина:").pack(side="left")
        self.bits_var = tk.StringVar(value=BITS_LABELS[0])
        ttk.Combobox(opts, textvariable=self.bits_var, values=BITS_LABELS, state="readonly", width=8).pack(side="left", padx=6)
        ttk.Button(opts, text="Сохранить каналы...", command=self.save).pack(side="left", padx=16)
        self.status = ttk.Label(self, text="", foreground="#2a6fd6", wraplength=900)
        self.status.pack(fill="x")

        self.apply_preset()

    @property
    def is_colour(self) -> bool:
        return self.roles[:3] == ("basecolor", "basecolor", "basecolor")

    def apply_preset(self) -> None:
        self.roles = PRESET_BY_NAME[self.preset_var.get()].roles
        for i, role in enumerate(self.roles):
            self.suffix[i].set(ROLE_SUFFIX[role])
            self.enabled[i].set(role != "none")
        self.hint.configure(
            text="R, G и B сохранятся одним цветным файлом с суффиксом из строки R." if self.is_colour else ""
        )

    def browse(self) -> None:
        name = filedialog.askopenfilename(title="Упакованная текстура", filetypes=FILE_TYPES)
        if name:
            self.load(Path(name))

    def _on_drop(self, paths) -> None:
        files = collect_images(paths)
        if files:
            self.load(files[0])

    def load(self, path: Path) -> None:
        try:
            self.planes = core.unpack(path)
            info = core.describe(path)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Не читается файл:\n{path}\n\n{exc}")
            return
        self.path = path
        self.drop_var.set(f"{path.name}   {info}")
        self.drop.configure(fg="#222222", bg="#eef5ff")
        self.thumbs = [to_photo(p, 110) for p in self.planes]
        for widget, img in zip(self.previews, self.thumbs):
            widget.configure(image=img, width=110, height=110)
        self.status.configure(text="")

    def _plane(self, i: int) -> np.ndarray:
        return 1.0 - self.planes[i] if self.invert[i].get() else self.planes[i]

    def save(self) -> None:
        if not self.planes or self.path is None:
            messagebox.showinfo(APP_TITLE, "Сначала положи текстуру.")
            return
        folder = filedialog.askdirectory(title="Папка для карт", initialdir=str(self.path.parent))
        if not folder:
            return
        base, _ = presets.split_name(self.path.stem)
        base = base or self.path.stem
        bits = 16 if self.bits_var.get().startswith("16") else 8

        # (file name, data) pairs; a base colour layout writes R, G and B as one image
        jobs: list[tuple[str, np.ndarray]] = []
        if self.is_colour and any(self.enabled[i].get() for i in range(3)):
            rgb = np.stack([self._plane(i) for i in range(3)], axis=2)
            jobs.append((f"{base}{self.suffix[0].get()}.png", rgb))
        for i in range(4):
            if self.is_colour and i < 3:
                continue
            if self.enabled[i].get():
                jobs.append((f"{base}{self.suffix[i].get()}.png", self._plane(i)))

        written, skipped = [], []
        for name, data in jobs:
            out = Path(folder) / name
            if same_file(out, self.path):
                skipped.append(name)
                continue
            core.save(out, data, bits=bits)
            written.append(out.name)
        text = "Сохранено: " + ", ".join(written) if written else "Нечего сохранять."
        if skipped:
            text += "  Пропущено, совпадает с исходником: " + ", ".join(skipped)
        self.status.configure(text=text)


@dataclass
class Job:
    """One texture the batch mode is going to write for a set."""

    title: str
    slots: list
    cells: list
    skip: str  # empty when the job will run, otherwise the reason it won't
    suffix: str
    drop_alpha: bool


class BatchTab(ttk.Frame):
    """Batch mode: drop a folder, get a mask and a base colour with alpha for every set."""

    def __init__(self, master):
        super().__init__(master, padding=12)
        self.sets: dict[str, dict[str, Path]] = {}
        self.root_dir: Path | None = None

        outs = ttk.LabelFrame(self, text="Что собирать", padding=8)
        outs.pack(fill="x")

        self.mask_on = tk.BooleanVar(value=True)
        ttk.Checkbutton(outs, text="Маска", variable=self.mask_on, command=self.refresh).grid(row=0, column=0, sticky="w")
        self.preset_var = tk.StringVar(value=MASK_PRESETS[0].name)
        combo = ttk.Combobox(outs, textvariable=self.preset_var, values=[p.name for p in MASK_PRESETS], state="readonly", width=34)
        combo.grid(row=0, column=1, padx=8, sticky="w")
        combo.bind("<<ComboboxSelected>>", lambda e: self.on_preset())
        ttk.Label(outs, text="суффикс:").grid(row=0, column=2, padx=(8, 4))
        self.mask_suffix = tk.StringVar(value=MASK_PRESETS[0].out_suffix)
        ttk.Entry(outs, textvariable=self.mask_suffix, width=20).grid(row=0, column=3, sticky="w")
        self.rgb_var = tk.BooleanVar(value=MASK_PRESETS[0].drop_alpha)
        ttk.Checkbutton(outs, text="без альфы (RGB)", variable=self.rgb_var).grid(row=0, column=4, padx=(12, 0))

        self.color_on = tk.BooleanVar(value=True)
        ttk.Checkbutton(outs, text="Base Color + альфа", variable=self.color_on, command=self.refresh).grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        ttk.Label(outs, text="только где нашлась opacity", foreground="#777777").grid(
            row=1, column=1, padx=8, sticky="w", pady=(6, 0)
        )
        ttk.Label(outs, text="суффикс:").grid(row=1, column=2, padx=(8, 4), pady=(6, 0))
        self.color_suffix = tk.StringVar(value=BASECOLOR_PRESET.out_suffix)
        ttk.Entry(outs, textvariable=self.color_suffix, width=20).grid(row=1, column=3, sticky="w", pady=(6, 0))

        self.drop_var = tk.StringVar(value="перетащи сюда папку с текстурами")
        self.drop = tk.Label(self, textvariable=self.drop_var, relief="groove", bg="#f4f4f4", fg="#777777", pady=18)
        self.drop.pack(fill="x", pady=10)
        self.drop.bind("<Button-1>", lambda e: self.browse())
        register_drop(self.drop, self._on_drop)
        register_drop(self, self._on_drop)

        table = ttk.Frame(self)
        table.pack(fill="both", expand=True)
        cols = ("R", "G", "B", "A")
        self.tree = ttk.Treeview(table, columns=cols, show="tree headings", height=10)
        self.tree.heading("#0", text="Набор / результат")
        self.tree.column("#0", width=270)
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=150)
        self.tree.tag_configure("skip", foreground="#999999")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")

        opts = ttk.Frame(self)
        opts.pack(fill="x", pady=10)
        ttk.Label(opts, text="Разрешение:").pack(side="left")
        self.size_var = tk.StringVar(value=SIZE_LABELS[0])
        ttk.Combobox(opts, textvariable=self.size_var, values=SIZE_LABELS, state="readonly", width=18).pack(side="left", padx=(6, 16))
        ttk.Label(opts, text="Глубина:").pack(side="left")
        self.bits_var = tk.StringVar(value=BITS_LABELS[0])
        ttk.Combobox(opts, textvariable=self.bits_var, values=BITS_LABELS, state="readonly", width=8).pack(side="left", padx=(6, 16))
        self.run_btn = ttk.Button(opts, text="Собрать всё", command=self.run)
        self.run_btn.pack(side="left")

        self.status = ttk.Label(self, text="", foreground="#2a6fd6", wraplength=900)
        self.status.pack(fill="x")

    def on_preset(self) -> None:
        preset = PRESET_BY_NAME[self.preset_var.get()]
        self.mask_suffix.set(preset.out_suffix)
        self.rgb_var.set(preset.drop_alpha)
        self.refresh()

    def browse(self) -> None:
        folder = filedialog.askdirectory(title="Папка с текстурами")
        if folder:
            self.load([Path(folder)])

    def _on_drop(self, paths) -> None:
        self.load(paths)

    def load(self, paths) -> None:
        files = collect_images(paths)
        if not files:
            self.status.configure(text="Картинок не нашлось.")
            return
        self.root_dir = paths[0] if paths[0].is_dir() else paths[0].parent
        self.drop_var.set(str(self.root_dir))
        self.drop.configure(fg="#222222", bg="#eef5ff")
        self.sets = presets.scan(files)
        self.refresh()

    @staticmethod
    def _slots(found: dict[str, Path], preset) -> tuple[list, list]:
        """Slots for one output texture plus the text shown in the table."""
        slots, cells = [], []
        for i, role in enumerate(preset.roles):
            path, invert = presets.pick_for_role(found, role)
            channel = "rgba"[i] if role == "basecolor" else "auto"
            slots.append(core.Slot(path=path, channel=channel, invert=invert, constant=ROLE_CONST[role]))
            if path and role == "basecolor" and i > 0:
                cells.append("то же")
            elif path:
                cells.append(path.name + (" (инверт)" if invert else ""))
            elif role == "none":
                cells.append("—")
            else:
                cells.append(f"заливка {ROLE_CONST[role]:.2f}")
        return slots, cells

    def plan(self, found: dict[str, Path]) -> list[Job]:
        """Everything this set will produce; refresh() shows it, run() writes it."""
        jobs = []
        if self.mask_on.get():
            preset = PRESET_BY_NAME[self.preset_var.get()]
            slots, cells = self._slots(found, preset)
            skip = "" if any(s.filled for s in slots) else "нет карт"
            jobs.append(Job("Маска", slots, cells, skip, self.mask_suffix.get(), self.rgb_var.get()))
        if self.color_on.get():
            slots, cells = self._slots(found, BASECOLOR_PRESET)
            if "basecolor" not in found:
                skip = "нет base color"
            elif "opacity" not in found:
                skip = "нет opacity"
            else:
                skip = ""
            jobs.append(Job("Base Color + альфа", slots, cells, skip, self.color_suffix.get(), False))
        return jobs

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        todo = 0
        for base, found in self.sets.items():
            parent = self.tree.insert("", "end", text=base, open=True)
            for job in self.plan(found):
                text = f"{job.title}: пропуск, {job.skip}" if job.skip else job.title
                self.tree.insert(parent, "end", text=text, values=job.cells, tags=("skip",) if job.skip else ())
                todo += 0 if job.skip else 1
        if self.sets:
            self.status.configure(text=f"Наборов найдено: {len(self.sets)}. Текстур к сборке: {todo}.")

    def run(self) -> None:
        if not self.sets:
            messagebox.showinfo(APP_TITLE, "Сначала положи папку с текстурами.")
            return
        out_dir = filedialog.askdirectory(title="Куда складывать результат", initialdir=str(self.root_dir or Path.home()))
        if not out_dir:
            return
        bits = 16 if self.bits_var.get().startswith("16") else 8
        mode = SIZE_VALUES[SIZE_LABELS.index(self.size_var.get())]
        self.run_btn.state(["disabled"])
        done, errors = 0, []
        for base, found in self.sets.items():
            for job in self.plan(found):
                if job.skip:
                    continue
                target = Path(out_dir) / f"{base}{job.suffix}.png"
                # never write over a source map of this set
                if any(same_file(target, p) for p in found.values()):
                    errors.append(f"{target.name}: совпадает с исходником")
                    continue
                self.status.configure(text=f"Считаю {target.name}...")
                self.update_idletasks()
                try:
                    size = core.target_size(job.slots, mode)
                    arr = core.pack(job.slots, size)
                    core.save(target, arr, bits=bits, drop_alpha=job.drop_alpha)
                    done += 1
                except Exception as exc:
                    errors.append(f"{target.name}: {exc}")
        self.run_btn.state(["!disabled"])
        text = f"Готово, собрано текстур: {done}."
        if errors:
            text += "  Ошибки: " + "; ".join(errors[:3])
        self.status.configure(text=text)


class App(BASE_TK):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1000x780")
        self.minsize(920, 720)
        try:
            ttk.Style(self).theme_use("vista")
        except Exception:
            pass

        book = ttk.Notebook(self)
        book.pack(fill="both", expand=True, padx=8, pady=8)
        book.add(PackTab(book), text="  Собрать  ")
        book.add(UnpackTab(book), text="  Разобрать  ")
        book.add(BatchTab(book), text="  Пакетно  ")

        note = "" if HAS_DND else "  Перетаскивание недоступно: не установлен tkinterdnd2. Файлы открываются кликом."
        ttk.Label(
            self,
            text="Каналы копируются как есть, без гамма-коррекции. sRGB в Unity: у масок выключить, у Base Color оставить."
            + note,
            foreground="#777777",
        ).pack(fill="x", padx=12, pady=(0, 8))

        self.report_callback_exception = self.on_error

    def on_error(self, exc, val, tb) -> None:
        text = "".join(traceback.format_exception(exc, val, tb))
        try:
            Path(__file__).with_name("error.log").write_text(text, encoding="utf-8")
        except Exception:
            pass
        messagebox.showerror(APP_TITLE, text[-1500:])


def main() -> None:
    try:
        App().mainloop()
    except Exception:
        text = traceback.format_exc()
        Path(__file__).with_name("error.log").write_text(text, encoding="utf-8")
        try:
            messagebox.showerror(APP_TITLE, text[-1500:])
        except Exception:
            print(text, file=sys.stderr)


if __name__ == "__main__":
    main()
