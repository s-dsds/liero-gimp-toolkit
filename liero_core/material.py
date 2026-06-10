from __future__ import annotations
import re
from dataclasses import dataclass
from .defaults import MATERIAL, MATERIAL_NAMES, DEFAULT_MATERIALS, DEFAULT_COLOR_ANIM, PREFERRED_REPLACEMENT_INDICES, PROTECTED_BY_DEFAULT

_NAME_RULES = [
    ("BG_SEESHADOW", ["see shadow", "seeshadow", "shadow bg", "bg shadow"]),
    ("BG_DIRT_2", ["bg dirt 2", "background dirt 2", "bg_dirt_2"]),
    ("BG_DIRT", ["bg dirt", "background dirt", "bg_dirt"]),
    ("DIRT_2", ["dirt 2", "dirt #2", "dirt_2"]),
    ("DIRT", ["dirt", "soil", "ground"]),
    ("ROCK", ["rock", "rocks", "stone"]),
    ("BG", ["background", "bg"]),
    ("WORM", ["worm", "worms"]),
    ("UNDEF", ["undef", "undefined", "shoot through", "shoot-through", "pass through"]),
]

def normalize_name(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[#_\-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def classify_name(name: str) -> int | None:
    s = normalize_name(name)
    for mat_name, needles in _NAME_RULES:
        if any(n in s for n in needles):
            return MATERIAL[mat_name]
    return None


def indices_for_material(material: int, material_table: list[int] | None = None) -> list[int]:
    table = material_table or DEFAULT_MATERIALS
    return [i for i, m in enumerate(table) if m == material]

@dataclass(frozen=True)
class IndexInfo:
    index: int
    material: int
    material_name: str
    animated: bool
    protected: bool
    preferred_replacement_candidate: bool


def index_info(index: int, material_table: list[int] | None = None) -> IndexInfo:
    table = material_table or DEFAULT_MATERIALS
    material = table[index]
    return IndexInfo(
        index=index,
        material=material,
        material_name=MATERIAL_NAMES.get(material, f"UNKNOWN_{material}"),
        animated=index in DEFAULT_COLOR_ANIM,
        protected=index in PROTECTED_BY_DEFAULT,
        preferred_replacement_candidate=index in PREFERRED_REPLACEMENT_INDICES,
    )
