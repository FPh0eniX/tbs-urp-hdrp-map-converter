"""URP Map Converter: сборка и разбор карт по каналам.

Окно на tkinter. Файлы можно перетаскивать мышкой прямо из проводника.
Логика работы с пикселями лежит в core.py, раскладки каналов в presets.py.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk

import core
import presets
from presets import PRESETS, PRESET_BY_NAME, ROLE_CONST, ROLE_SUFFIX, ROLE_TITLES

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    BASE_TK = TkinterDnD.Tk
    HAS_DND = True
except Exception:  # библиотека не поставлена, работаем без перетаскивания
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


def register_drop(widget, callback) -> None:
    """Вешает на виджет приём файлов из проводника."""
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
    """Разворачивает папки в список картинок."""
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            out += sorted(f for f in p.rglob("*") if f.is_file() and core.is_image(f))
        elif p.is_file() and core.is_image(p):
            out.append(p)
    return out


def to_photo(plane_or_rgb: np.ndarray, box: int) -> ImageTk.PhotoImage:
    """Готовит превью для показа в окне."""
    data = np.clip(np.rint(plane_or_rgb * 255), 0, 255).astype(np.uint8)
    mode = "L" if data.ndim == 2 else "RGB"
    img = Image.fromarray(data, mode=mode)
    img.thumbnail((box, box), Image.NEAREST)
    return ImageTk.PhotoImage(img)


class SlotRow(ttk.Frame):
    """Одна строка настроек канала результата."""

    def __init__(self, master, index: int, on_change):
        super().__init__(master, padding=(0, 3))
        self.index = index
        self.on_change = on_change
        self.slot = core.Slot()
        self.role = "none"

        letter, color = LETTERS[index]
        tk.Label(self, text=letter, fg=color, width=2, font=("Segoe UI", 12, "bold")).grid(row=0, column=0)

        self.role_var = tk.StringVar(value=ROLE_TITLES["none"])
        ttk.Label(self, textvariable=self.role_var, width=13).grid(row=0, column=1, sticky="w")

        self.file_var = tk.StringVar(value="перетащи файл сюда")
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

    # --- состояние ---

    def set_role(self, role: str) -> None:
        self.role = role
        self.role_var.set(ROLE_TITLES[role])
        if not self.slot.filled:
            self.const_var.set(f"{ROLE_CONST[role]:.2f}")
        self._apply()

    def set_file(self, path: Path) -> None:
        self.slot.path = path
        try:
            info = core.describe(path)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Не читается файл:\n{path}\n\n{exc}")
            self.slot.path = None
            return
        self.file_var.set(f"{path.name}   {info}")
        self.drop.configure(fg="#222222", bg="#eef5ff")
        # roughness в слоте smoothness сам предлагает инверсию
        _, role = presets.split_name(path.stem)
        if role and self.role in ("smoothness", "roughness") and role != self.role:
            if {role, self.role} == {"smoothness", "roughness"}:
                self.invert_var.set(True)
        self._apply()

    def clear(self) -> None:
        self.slot.path = None
        self.invert_var.set(False)
        self.file_var.set("перетащи файл сюда")
        self.drop.configure(fg="#777777", bg="#f4f4f4")
        self.const_var.set(f"{ROLE_CONST[self.role]:.2f}")
        self._apply()

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
    """Собрать четыре карты в одну текстуру."""

    def __init__(self, master):
        super().__init__(master, padding=12)
        self.preview_ids = []

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="Раскладка:").pack(side="left")
        self.preset_var = tk.StringVar(value=PRESETS[0].name)
        combo = ttk.Combobox(top, textvariable=self.preset_var, values=[p.name for p in PRESETS], state="readonly", width=32)
        combo.pack(side="left", padx=8)
        combo.bind("<<ComboboxSelected>>", lambda e: self.apply_preset())

        self.hint = ttk.Label(self, text="", wraplength=880, foreground="#555555")
        self.hint.pack(fill="x", pady=(6, 10))

        rows = ttk.LabelFrame(self, text="Каналы результата", padding=8)
        rows.pack(fill="x")
        head = ttk.Frame(rows)
        head.pack(fill="x")
        ttk.Label(head, text="канал      карта", foreground="#888888").pack(side="left")
        ttk.Label(head, text="файл · откуда брать · инверсия · заливка", foreground="#888888").pack(side="right")
        self.rows = []
        for i in range(4):
            row = SlotRow(rows, i, self.schedule_preview)
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

    def drop_many(self, paths) -> None:
        """Несколько файлов сразу: раскидываем по ролям пресета."""
        files = collect_images(paths)
        if not files:
            return
        if len(files) == 1:
            for row in self.rows:
                if not row.slot.filled:
                    row.set_file(files[0])
                    return
            self.rows[0].set_file(files[0])
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
                row.set_file(path)
                row.invert_var.set(invert)
                row._apply()
                used = True
        if not used:
            self.status.configure(text="По именам файлов роли не угадались, разложи вручную.")

    def reset(self) -> None:
        for row in self.rows:
            row.clear()
        self.status.configure(text="")

    def schedule_preview(self) -> None:
        if getattr(self, "_pending", None):
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
        suggested = f"{base}_Mask.png"
        name = filedialog.asksaveasfilename(
            title="Куда сохранить",
            initialdir=str(first.parent),
            initialfile=suggested,
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("TGA", "*.tga"), ("TIFF", "*.tif")],
        )
        if not name:
            return
        bits = 16 if self.bits_var.get().startswith("16") else 8
        mode = SIZE_VALUES[SIZE_LABELS.index(self.size_var.get())]
        size = core.target_size(slots, mode)
        self.status.configure(text="Считаю...")
        self.update_idletasks()
        try:
            arr = core.pack(slots, size)
            core.save(Path(name), arr, bits=bits, drop_alpha=self.rgb_var.get())
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Не сохранилось:\n{exc}")
            self.status.configure(text="")
            return
        self.status.configure(text=f"Готово: {Path(name).name}  {size[0]}x{size[1]}, {bits} бит")


class UnpackTab(ttk.Frame):
    """Разобрать упакованную текстуру обратно на отдельные карты."""

    def __init__(self, master):
        super().__init__(master, padding=12)
        self.path: Path | None = None
        self.planes: list[np.ndarray] = []
        self.thumbs: list[ImageTk.PhotoImage] = []

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="Раскладка:").pack(side="left")
        self.preset_var = tk.StringVar(value=PRESETS[0].name)
        combo = ttk.Combobox(top, textvariable=self.preset_var, values=[p.name for p in PRESETS], state="readonly", width=32)
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

        opts = ttk.Frame(self)
        opts.pack(fill="x", pady=10)
        ttk.Label(opts, text="Глубина:").pack(side="left")
        self.bits_var = tk.StringVar(value=BITS_LABELS[0])
        ttk.Combobox(opts, textvariable=self.bits_var, values=BITS_LABELS, state="readonly", width=8).pack(side="left", padx=6)
        ttk.Button(opts, text="Сохранить каналы...", command=self.save).pack(side="left", padx=16)
        self.status = ttk.Label(self, text="", foreground="#2a6fd6", wraplength=880)
        self.status.pack(fill="x")

        self.apply_preset()

    def apply_preset(self) -> None:
        for i, role in enumerate(PRESET_BY_NAME[self.preset_var.get()].roles):
            self.suffix[i].set(ROLE_SUFFIX[role])
            self.enabled[i].set(role != "none")

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
        written = []
        for i, plane in enumerate(self.planes):
            if not self.enabled[i].get():
                continue
            data = 1.0 - plane if self.invert[i].get() else plane
            out = Path(folder) / f"{base}{self.suffix[i].get()}.png"
            core.save(out, data, bits=bits)
            written.append(out.name)
        self.status.configure(text="Сохранено: " + ", ".join(written) if written else "Нечего сохранять.")


class BatchTab(ttk.Frame):
    """Пакетная сборка: кидаешь папку, получаешь маски для всех наборов."""

    def __init__(self, master):
        super().__init__(master, padding=12)
        self.sets: dict[str, dict[str, Path]] = {}
        self.root_dir: Path | None = None

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="Раскладка:").pack(side="left")
        self.preset_var = tk.StringVar(value=PRESETS[0].name)
        combo = ttk.Combobox(top, textvariable=self.preset_var, values=[p.name for p in PRESETS], state="readonly", width=32)
        combo.pack(side="left", padx=8)
        combo.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Label(top, text="Суффикс:").pack(side="left", padx=(16, 4))
        self.suffix_var = tk.StringVar(value="_Mask")
        ttk.Entry(top, textvariable=self.suffix_var, width=10).pack(side="left")

        self.drop_var = tk.StringVar(value="перетащи сюда папку с текстурами")
        self.drop = tk.Label(self, textvariable=self.drop_var, relief="groove", bg="#f4f4f4", fg="#777777", pady=18)
        self.drop.pack(fill="x", pady=10)
        self.drop.bind("<Button-1>", lambda e: self.browse())
        register_drop(self.drop, self._on_drop)
        register_drop(self, self._on_drop)

        cols = ("R", "G", "B", "A")
        self.tree = ttk.Treeview(self, columns=cols, show="tree headings", height=9)
        self.tree.heading("#0", text="Набор")
        self.tree.column("#0", width=240)
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=150)
        self.tree.pack(fill="both", expand=True)

        opts = ttk.Frame(self)
        opts.pack(fill="x", pady=10)
        ttk.Label(opts, text="Разрешение:").pack(side="left")
        self.size_var = tk.StringVar(value=SIZE_LABELS[0])
        ttk.Combobox(opts, textvariable=self.size_var, values=SIZE_LABELS, state="readonly", width=18).pack(side="left", padx=(6, 16))
        ttk.Label(opts, text="Глубина:").pack(side="left")
        self.bits_var = tk.StringVar(value=BITS_LABELS[0])
        ttk.Combobox(opts, textvariable=self.bits_var, values=BITS_LABELS, state="readonly", width=8).pack(side="left", padx=(6, 16))
        self.rgb_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="без альфы (RGB)", variable=self.rgb_var).pack(side="left", padx=(0, 16))
        self.run_btn = ttk.Button(opts, text="Собрать всё", command=self.run)
        self.run_btn.pack(side="left")

        self.status = ttk.Label(self, text="", foreground="#2a6fd6", wraplength=880)
        self.status.pack(fill="x")

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

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        preset = PRESET_BY_NAME[self.preset_var.get()]
        ready = 0
        for base, found in self.sets.items():
            cells = []
            filled = 0
            for role in preset.roles:
                path, invert = presets.pick_for_role(found, role)
                if path:
                    cells.append(path.name + (" (инверт)" if invert else ""))
                    filled += 1
                else:
                    cells.append(f"заливка {ROLE_CONST[role]:.2f}")
            self.tree.insert("", "end", text=base, values=cells)
            if filled:
                ready += 1
        self.status.configure(text=f"Наборов найдено: {len(self.sets)}, из них с картами: {ready}")

    def run(self) -> None:
        if not self.sets:
            messagebox.showinfo(APP_TITLE, "Сначала положи папку с текстурами.")
            return
        out_dir = filedialog.askdirectory(title="Куда складывать результат", initialdir=str(self.root_dir or Path.home()))
        if not out_dir:
            return
        preset = PRESET_BY_NAME[self.preset_var.get()]
        bits = 16 if self.bits_var.get().startswith("16") else 8
        mode = SIZE_VALUES[SIZE_LABELS.index(self.size_var.get())]
        self.run_btn.state(["disabled"])
        done, errors = 0, []
        for base, found in self.sets.items():
            slots = []
            for role in preset.roles:
                path, invert = presets.pick_for_role(found, role)
                slots.append(core.Slot(path=path, channel="auto", invert=invert, constant=ROLE_CONST[role]))
            if not any(s.filled for s in slots):
                continue
            self.status.configure(text=f"Считаю {base}...")
            self.update_idletasks()
            try:
                size = core.target_size(slots, mode)
                arr = core.pack(slots, size)
                core.save(Path(out_dir) / f"{base}{self.suffix_var.get()}.png", arr, bits=bits, drop_alpha=self.rgb_var.get())
                done += 1
            except Exception as exc:
                errors.append(f"{base}: {exc}")
        self.run_btn.state(["!disabled"])
        text = f"Готово, собрано текстур: {done}."
        if errors:
            text += "  Ошибки: " + "; ".join(errors[:3])
        self.status.configure(text=text)


class App(BASE_TK):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x760")
        self.minsize(900, 700)
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
            text="Каналы копируются как есть, без гаммы. В Unity у готовой карты сними галку sRGB." + note,
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
