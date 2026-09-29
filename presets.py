"""Channel layout presets and file name parsing for the batch mode."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Map roles. "none" means the channel is unused in this layout.
# "basecolor" is special: a row with this role takes the channel that matches
# its own letter (R from R, G from G, B from B), so three rows together copy
# the colour image as is.
ROLE_TITLES = {
    "basecolor": "Base Color",
    "opacity": "Opacity",
    "metallic": "Metallic",
    "ao": "AO",
    "detail": "Detail",
    "smoothness": "Smoothness",
    "roughness": "Roughness",
    "height": "Height",
    "none": "свободный",
}

# Fill value when the map is missing.
ROLE_CONST = {
    "basecolor": 0.5,
    "opacity": 1.0,
    "metallic": 0.0,
    "ao": 1.0,
    "detail": 0.0,
    "smoothness": 0.5,
    "roughness": 0.5,
    "height": 0.5,
    "none": 0.0,
}

ROLE_SUFFIX = {
    "basecolor": "_BaseColor",
    "opacity": "_Opacity",
    "metallic": "_Metallic",
    "ao": "_AO",
    "detail": "_Detail",
    "smoothness": "_Smoothness",
    "roughness": "_Roughness",
    "height": "_Height",
    "none": "_Channel",
}


@dataclass(frozen=True)
class Preset:
    name: str
    roles: tuple[str, str, str, str]
    hint: str = ""
    drop_alpha: bool = False
    out_suffix: str = "_Packed"

    @property
    def is_basecolor(self) -> bool:
        return self.roles[:3] == ("basecolor", "basecolor", "basecolor")


PRESETS = (
    Preset(
        "Unity Mask Map (URP / HDRP)",
        ("metallic", "ao", "detail", "smoothness"),
        "В URP положи готовую текстуру и в слот Metallic, и в слот Occlusion, "
        "а в импорте сними галку sRGB. Синий канал читает только HDRP.",
        out_suffix="_Mask",
    ),
    Preset(
        "Base Color + Opacity (URP / HDRP)",
        ("basecolor", "basecolor", "basecolor", "opacity"),
        "RGB берётся из base color как есть, в альфу идёт opacity. Галку sRGB в импорте "
        "оставь включённой, на альфу она не влияет. В материале включи Alpha Clipping "
        "или Surface Type Transparent, Smoothness Source оставь Metallic Alpha.",
        out_suffix="_BaseColorAlpha",
    ),
    Preset(
        "URP Metallic Smoothness",
        ("metallic", "none", "none", "smoothness"),
        "Классическая карта URP Lit: металл в красном, гладкость в альфе.",
        out_suffix="_MetallicSmoothness",
    ),
    Preset(
        "ORM (Unreal, glTF)",
        ("ao", "roughness", "metallic", "none"),
        "Стандарт Unreal и glTF. Альфа не нужна, сохраняй как RGB.",
        drop_alpha=True,
        out_suffix="_ORM",
    ),
    Preset(
        "RMA",
        ("roughness", "metallic", "ao", "none"),
        "Раскладка части ассетов с маркетплейсов.",
        drop_alpha=True,
        out_suffix="_RMA",
    ),
    Preset(
        "Свободная раскладка",
        ("none", "none", "none", "none"),
        "Каналы настраиваешь сам.",
    ),
)

PRESET_BY_NAME = {p.name: p for p in PRESETS}
BASECOLOR_PRESET = next(p for p in PRESETS if p.is_basecolor)
MASK_PRESETS = tuple(p for p in PRESETS if not p.is_basecolor)

# --- file name parsing ----------------------------------------------------

ROLE_TOKENS = {
    "basecolor": (
        "basecolor",
        "basecolour",
        "basemap",
        "albedo",
        "diffuse",
        "diff",
        "color",
        "colour",
        "col",
        "bc",
        # colour that already carries alpha, e.g. our own output or Unity's
        # AlbedoTransparency; listed here so the alpha word does not win
        "basecoloralpha",
        "basecoloropacity",
        "albedoalpha",
        "albedotransparency",
        "diffusealpha",
    ),
    "opacity": ("opacity", "opacitymask", "alpha", "transparency", "cutout"),
    "metallic": ("metallic", "metalness", "metal", "mtl", "m"),
    "ao": ("ambientocclusion", "occlusion", "ao", "occ"),
    "roughness": ("roughness", "rough", "rgh"),
    "smoothness": ("smoothness", "smooth", "glossiness", "gloss"),
    "detail": ("detailmask", "detail"),
    "height": ("height", "displacement", "disp"),
}

# Base colour that already has alpha packed in. It still counts as base colour,
# but a plain base colour of the same set wins, so a second batch run over its
# own output folder keeps reading the original file.
_PACKED_COLOUR = {
    "basecoloralpha",
    "basecoloropacity",
    "albedoalpha",
    "albedotransparency",
    "diffusealpha",
}

_SEP = re.compile(r"[ _\-.]+")


def _match(stem: str) -> tuple[str, str | None, str]:
    """Set name, role and the word that matched ('' when nothing did)."""
    tokens = [t for t in _SEP.split(stem) if t]
    if not tokens:
        return stem, None, ""

    # two-word endings first, so 'Base_Color' is not read as just 'Color'
    for take in (2, 1):
        if len(tokens) < take:
            continue
        tail = "".join(tokens[-take:]).lower()
        for role, words in ROLE_TOKENS.items():
            if tail in words:
                base = "_".join(tokens[:-take])
                return (base or stem), role, tail

    # glued names like RockMetallic
    last = tokens[-1].lower()
    for role, words in ROLE_TOKENS.items():
        for word in words:
            if len(word) > 3 and last.endswith(word) and len(last) > len(word):
                head = tokens[-1][: -len(word)]
                base = "_".join(tokens[:-1] + [head]).strip("_")
                return (base or stem), role, word
    return stem, None, ""


def split_name(stem: str) -> tuple[str, str | None]:
    """Split a file name into the set name and the map role.

    'T_Rock_01_Ambient_Occlusion' -> ('T_Rock_01', 'ao')
    'T_Leaf_Base_Color' -> ('T_Leaf', 'basecolor')
    'rock_normal' -> ('rock_normal', None)
    """
    base, role, _ = _match(stem)
    return base, role


def scan(paths) -> dict[str, dict[str, Path]]:
    """Group files into sets: set name -> {role: path}."""
    sets: dict[str, dict[str, Path]] = {}
    ranks: dict[tuple[str, str], int] = {}
    for path in sorted(paths):
        base, role, word = _match(path.stem)
        if role is None:
            continue
        rank = 1 if word in _PACKED_COLOUR else 0
        key = (base, role)
        if key not in ranks or rank < ranks[key]:
            ranks[key] = rank
            sets.setdefault(base, {})[role] = path
    return dict(sorted(sets.items()))


def pick_for_role(found: dict[str, Path], role: str) -> tuple[Path | None, bool]:
    """Find a file for a channel role. Returns the path and whether to invert it.

    Roughness and smoothness are the same data inverted, so either one will do.
    """
    if role == "none":
        return None, False
    if role in found:
        return found[role], False
    pair = {"smoothness": "roughness", "roughness": "smoothness"}.get(role)
    if pair and pair in found:
        return found[pair], True
    return None, False
