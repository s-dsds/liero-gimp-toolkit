from __future__ import annotations
import re
from dataclasses import dataclass
from .defaults import MATERIAL, MATERIAL_NAMES, DEFAULT_MATERIALS, ANIMATED_INDICES, PREFERRED_REPLACEMENT_INDICES

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
    animated = index in ANIMATED_INDICES
    return IndexInfo(
        index=index,
        material=material,
        material_name=MATERIAL_NAMES.get(material, f"UNKNOWN_{material}"),
        animated=animated,
        # protected = animated or worm, derived from the table in use so that
        # custom WebLiero Extended tables protect *their* worm indices.
        protected=animated or material == MATERIAL["WORM"],
        preferred_replacement_candidate=index in PREFERRED_REPLACEMENT_INDICES,
    )


# --- WebLiero Extended custom material tables --------------------------------
#
# WLE room scripts pass a plain 256-int array to WLROOM.setMaterials(), usually
# written as `defaultMaterials.map(...)` helper chains in mapsettings.js.

_KNOWN_MATERIAL_VALUES = set(MATERIAL.values())


def _validate_material_table(values: list[int], source: str) -> list[int]:
    if len(values) < 256:
        raise ValueError(f"Material table needs at least 256 entries, got {len(values)}: {source}")
    values = [int(v) for v in values[:256]]
    unknown = sorted({v for v in values if v not in _KNOWN_MATERIAL_VALUES})
    if unknown:
        raise ValueError(f"Unknown material values {unknown} in table: {source}")
    return values


def load_material_table(path) -> list[int]:
    """Load a 256-entry material table from a JSON array file or a JS-style
    snippet (e.g. an array pasted out of a WLE room script)."""
    import json
    import re as _re
    from pathlib import Path as _Path
    path = _Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return _validate_material_table(data, str(path))
    except ValueError:
        pass
    # JS-ish fallback: strip comments, take the first numeric array with >= 256 entries.
    stripped = _re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=_re.S)
    for match in _re.finditer(r"\[([\s\d,]+)\]", stripped):
        values = [int(v) for v in match.group(1).replace("\n", " ").split(",") if v.strip()]
        if len(values) >= 256:
            return _validate_material_table(values, str(path))
    raise ValueError(f"No 256-entry material array found in: {path}")


def material_table_to_js(table: list[int], base: list[int] | None = None) -> str:
    """Render a material table as a paste-ready WLE room-script expression.

    Diffs against ``base`` (default: classic table) and emits
    ``defaultMaterials.map(replaceMatIndexBy(...))`` chains, recognizing the
    common ``noUndef`` (all UNDEF -> ROCK) helper first. Falls back to a plain
    array literal when the table doesn't derive from the default one.
    """
    base = list(base or DEFAULT_MATERIALS)
    table = list(table[:256])
    expr = "defaultMaterials"
    work = base
    undef_idxs = [i for i, m in enumerate(base) if m == MATERIAL["UNDEF"]]
    if undef_idxs and all(table[i] == MATERIAL["ROCK"] for i in undef_idxs):
        expr += ".map(noUndef)"
        work = [MATERIAL["ROCK"] if m == MATERIAL["UNDEF"] else m for m in work]
    changes: dict[int, list[int]] = {}
    for i in range(256):
        if table[i] != work[i]:
            changes.setdefault(table[i], []).append(i)
    if sum(len(v) for v in changes.values()) > 64:
        return "[" + ",".join(str(v) for v in table) + "]"
    for mat_value, idxs in sorted(changes.items()):
        name = MATERIAL_NAMES.get(mat_value, str(mat_value))
        mat_ref = f"MATERIAL.{name}" if mat_value in MATERIAL_NAMES else str(mat_value)
        expr += f".map(replaceMatIndexBy({mat_ref},{','.join(str(i) for i in idxs)}))"
    return expr
