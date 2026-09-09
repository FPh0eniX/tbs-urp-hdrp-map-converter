"""Ядро конвертера: чтение карт, сборка каналов и разбор упакованной текстуры.

Здесь нет ничего от интерфейса, только работа с пикселями.
Внутри все карты держим как float32 в диапазоне 0..1: так не теряется точность
при ресайзе, инверсии и переходе между 8 и 16 битами.

Важно: никаких гамма-преобразований тут нет. Каналы копируются как есть,
потому что metallic, AO, roughness и smoothness это данные, а не цвет.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

READ_EXT = (".png", ".tga", ".tif", ".tiff", ".jpg", ".jpeg", ".bmp", ".webp")
CHANNELS = ("auto", "r", "g", "b", "a")

# Кэш загруженных картинок, чтобы превью не перечитывало файлы каждый раз.
_CACHE: dict[tuple[str, float], np.ndarray] = {}
_CACHE_LIMIT = 8


def is_image(path: Path) -> bool:
    return path.suffix.lower() in READ_EXT


def load(path: Path) -> np.ndarray:
    """Читает файл и возвращает массив H x W x C, float32 0..1."""
    path = Path(path)
    key = (str(path), path.stat().st_mtime)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    img = Image.open(path)
    img.load()
    mode = img.mode

    if mode in ("I;16", "I;16B", "I;16L", "I;16N"):
        arr = np.asarray(img).astype(np.float32) / 65535.0
        arr = arr[..., None]
    elif mode == "I":
        arr = np.asarray(img).astype(np.float32) / 65535.0
        arr = arr[..., None]
    elif mode == "F":
        arr = np.asarray(img).astype(np.float32)[..., None]
    else:
        if mode not in ("L", "LA", "RGB", "RGBA"):
            img = img.convert("RGBA")
        arr = np.asarray(img).astype(np.float32) / 255.0
        if arr.ndim == 2:
            arr = arr[..., None]

    arr = np.ascontiguousarray(arr)
    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = arr
    return arr


def describe(path: Path) -> str:
    """Короткая подпись под файлом: размер и разрядность."""
    with Image.open(path) as img:
        w, h = img.size
        bits = 16 if img.mode.startswith("I") or img.mode == "F" else 8
        ch = {"L": 1, "I": 1, "F": 1, "LA": 2, "RGB": 3, "RGBA": 4}.get(img.mode, 4)
    if img.mode.startswith("I;16"):
        bits = 16
    kind = "серая" if ch <= 2 else ("RGB" if ch == 3 else "RGBA")
    return f"{w}x{h} · {bits} бит · {kind}"


def size_of(path: Path) -> tuple[int, int]:
    with Image.open(path) as img:
        return img.size


def extract(arr: np.ndarray, channel: str) -> np.ndarray:
    """Достаёт один канал как плоскость H x W."""
    c = arr.shape[2]
    if channel == "auto":
        return arr[..., :3].mean(axis=2) if c >= 3 else arr[..., 0]
    idx = "rgba".index(channel)
    if idx < c:
        return arr[..., idx]
    if channel == "a":
        # альфы в файле нет, значит непрозрачно
        return np.ones(arr.shape[:2], dtype=np.float32)
    return arr[..., min(idx, c - 1)]


def fit(plane: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Приводит плоскость к нужному разрешению."""
    h, w = plane.shape
    if (w, h) == size:
        return plane
    img = Image.fromarray(plane.astype(np.float32), mode="F")
    return np.asarray(img.resize(size, Image.LANCZOS), dtype=np.float32)


@dataclass
class Slot:
    """Один канал результата: откуда берём и что с ним делаем."""

    path: Path | None = None
    channel: str = "auto"
    invert: bool = False
    constant: float = 0.0

    @property
    def filled(self) -> bool:
        return self.path is not None

    def plane(self, size: tuple[int, int]) -> np.ndarray:
        if self.path is None:
            return np.full((size[1], size[0]), float(self.constant), dtype=np.float32)
        p = fit(extract(load(self.path), self.channel), size)
        if self.invert:
            p = 1.0 - p
        return np.clip(p, 0.0, 1.0)

    def source_size(self) -> tuple[int, int] | None:
        return size_of(self.path) if self.path else None


def target_size(slots, mode: str) -> tuple[int, int]:
    """Разрешение результата. mode: 'max', 'min' или число вроде '2048'."""
    if mode not in ("max", "min"):
        n = int(mode)
        return (n, n)
    sizes = [s.source_size() for s in slots if s.filled]
    sizes = [s for s in sizes if s]
    if not sizes:
        return (1024, 1024)
    pick = max if mode == "max" else min
    return (pick(s[0] for s in sizes), pick(s[1] for s in sizes))


def pack(slots, size: tuple[int, int]) -> np.ndarray:
    """Собирает четыре канала в один массив H x W x 4."""
    return np.stack([s.plane(size) for s in slots], axis=2)


def unpack(path: Path) -> list[np.ndarray]:
    """Разбирает текстуру на четыре плоскости."""
    arr = load(path)
    return [extract(arr, c) for c in ("r", "g", "b", "a")]


# --- запись ---------------------------------------------------------------


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _write_png16(path: Path, arr: np.ndarray) -> None:
    """Своя запись 16-битного PNG: Pillow умеет только серые 16 бит."""
    if arr.ndim == 2:
        arr = arr[..., None]
    h, w, c = arr.shape
    color_type = {1: 0, 2: 4, 3: 2, 4: 6}[c]
    rows = arr.astype(">u2").tobytes()
    stride = w * c * 2
    raw = b"".join(b"\x00" + rows[i * stride : (i + 1) * stride] for i in range(h))
    out = b"\x89PNG\r\n\x1a\n"
    out += _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, color_type, 0, 0, 0))
    out += _chunk(b"IDAT", zlib.compress(raw, 9))
    out += _chunk(b"IEND", b"")
    Path(path).write_bytes(out)


def _to_int(arr: np.ndarray, bits: int) -> np.ndarray:
    top = 65535 if bits == 16 else 255
    dt = np.uint16 if bits == 16 else np.uint8
    return np.clip(np.rint(arr * top), 0, top).astype(dt)


def save(path: Path, arr: np.ndarray, bits: int = 8, drop_alpha: bool = False) -> None:
    """Сохраняет результат. arr это H x W x 4 или H x W (серая карта)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if arr.ndim == 3 and drop_alpha:
        arr = arr[..., :3]

    ext = path.suffix.lower()
    if bits == 16:
        if ext != ".png":
            path = path.with_suffix(".png")
        _write_png16(path, _to_int(arr, 16))
        return

    data = _to_int(arr, 8)
    if data.ndim == 2:
        mode = "L"
    else:
        mode = {1: "L", 3: "RGB", 4: "RGBA"}[data.shape[2]]
        if mode == "L":
            data = data[..., 0]
    Image.fromarray(data, mode=mode).save(path)
