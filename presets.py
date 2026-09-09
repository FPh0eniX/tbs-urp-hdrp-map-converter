"""Пресеты раскладки каналов и разбор имён файлов для пакетного режима."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Роли карт. "none" значит, что канал в этой раскладке не используется.
ROLE_TITLES = {
    "metallic": "Metallic",
    "ao": "AO",
    "detail": "Detail",
    "smoothness": "Smoothness",
    "roughness": "Roughness",
    "height": "Height",
    "opacity": "Opacity",
    "none": "свободный",
}

# Чем заполнить канал, если файла нет.
ROLE_CONST = {
    "metallic": 0.0,
    "ao": 1.0,
    "detail": 0.0,
    "smoothness": 0.5,
    "roughness": 0.5,
    "height": 0.5,
    "opacity": 1.0,
    "none": 0.0,
}

ROLE_SUFFIX = {
    "metallic": "_Metallic",
    "ao": "_AO",
    "detail": "_Detail",
    "smoothness": "_Smoothness",
    "roughness": "_Roughness",
    "height": "_Height",
    "opacity": "_Opacity",
    "none": "_Channel",
}


@dataclass(frozen=True)
class Preset:
    name: str
    roles: tuple[str, str, str, str]
    hint: str = ""
    drop_alpha: bool = False


PRESETS = (
    Preset(
        "Unity Mask Map (URP / HDRP)",
        ("metallic", "ao", "detail", "smoothness"),
        "В URP положи готовую текстуру и в слот Metallic, и в слот Occlusion, "
        "а в импорте сними галку sRGB. Синий канал читает только HDRP.",
    ),
    Preset(
        "URP Metallic Smoothness",
        ("metallic", "none", "none", "smoothness"),
        "Классическая карта URP Lit: металл в красном, гладкость в альфе.",
    ),
    Preset(
        "ORM (Unreal, glTF)",
        ("ao", "roughness", "metallic", "none"),
        "Стандарт Unreal и glTF. Альфа не нужна, сохраняй как RGB.",
        drop_alpha=True,
    ),
    Preset(
        "RMA",
        ("roughness", "metallic", "ao", "none"),
        "Раскладка части ассетов с маркетплейсов.",
        drop_alpha=True,
    ),
    Preset(
        "Свободная раскладка",
        ("none", "none", "none", "none"),
        "Каналы настраиваешь сам.",
    ),
)

PRESET_BY_NAME = {p.name: p for p in PRESETS}

# --- распознавание имён файлов -------------------------------------------

ROLE_TOKENS = {
    "metallic": ("metallic", "metalness", "metal", "mtl", "m"),
    "ao": ("ambientocclusion", "occlusion", "ao", "occ"),
    "roughness": ("roughness", "rough", "rgh"),
    "smoothness": ("smoothness", "smooth", "glossiness", "gloss"),
    "detail": ("detailmask", "detail"),
    "height": ("height", "displacement", "disp"),
    "opacity": ("opacity", "alpha", "transparency"),
}

_SEP = re.compile(r"[ _\-.]+")


def split_name(stem: str) -> tuple[str, str | None]:
    """Делит имя файла на базовое имя набора и роль карты.

    'T_Rock_01_Ambient_Occlusion' -> ('T_Rock_01', 'ao')
    'rock_basecolor' -> ('rock_basecolor', None)
    """
    tokens = [t for t in _SEP.split(stem) if t]
    if not tokens:
        return stem, None

    for take in (2, 1):
        if len(tokens) < take:
            continue
        tail = "".join(tokens[-take:]).lower()
        for role, words in ROLE_TOKENS.items():
            if tail in words:
                base = "_".join(tokens[:-take])
                return (base or stem), role

    # склеенные имена вида RockMetallic
    last = tokens[-1].lower()
    for role, words in ROLE_TOKENS.items():
        for word in words:
            if len(word) > 3 and last.endswith(word) and len(last) > len(word):
                head = tokens[-1][: -len(word)]
                base = "_".join(tokens[:-1] + [head]).strip("_")
                return (base or stem), role
    return stem, None


def scan(paths) -> dict[str, dict[str, Path]]:
    """Группирует файлы в наборы: базовое имя -> {роль: путь}."""
    sets: dict[str, dict[str, Path]] = {}
    for path in paths:
        base, role = split_name(path.stem)
        if role is None:
            continue
        sets.setdefault(base, {}).setdefault(role, path)
    return dict(sorted(sets.items()))


def pick_for_role(found: dict[str, Path], role: str) -> tuple[Path | None, bool]:
    """Ищет файл под роль канала. Возвращает путь и надо ли инвертировать.

    Roughness и smoothness взаимозаменяемы через инверсию, этим и пользуемся.
    """
    if role == "none":
        return None, False
    if role in found:
        return found[role], False
    pair = {"smoothness": "roughness", "roughness": "smoothness"}.get(role)
    if pair and pair in found:
        return found[pair], True
    return None, False
