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
    return IndexInfo(
        index=index,
        material=material,
        material_name=MATERIAL_NAMES.get(material, f"UNKNOWN_{material}"),
        animated=index in ANIMATED_INDICES,
        # Only worm indices are protected (derived from the table in use, so
        # custom WLE tables protect *their* worm slots). Animated indices are
        # informational: colorAnim is mod-configurable.
        protected=material == MATERIAL["WORM"],
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


def _apply_map_helper(table: list[int], arg: str, source: str) -> list[int]:
    """Apply one room-script ``.map(<helper>)`` step to a material table."""
    arg = arg.strip()
    if arg == "noUndef":
        return [MATERIAL["ROCK"] if m == MATERIAL["UNDEF"] else m for m in table]
    if arg == "undefToDirt":
        return [MATERIAL["DIRT"] if m == MATERIAL["UNDEF"] else m for m in table]
    m = re.match(r"replaceMatIndexBy\s*\(\s*(MATERIAL\.\w+|\d+)\s*,(.*)\)\s*$", arg, re.S)
    if m:
        token = m.group(1)
        if token.startswith("MATERIAL."):
            mat_name = token.split(".", 1)[1]
            if mat_name not in MATERIAL:
                raise ValueError(f"Unknown material {token} in: {source}")
            value = MATERIAL[mat_name]
        else:
            value = int(token)
        out = list(table)
        for part in re.finditer(r"\.\.\._range\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)|(\d+)", m.group(2)):
            if part.group(3) is not None:
                idxs = [int(part.group(3))]
            else:
                idxs = range(int(part.group(1)), int(part.group(2)) + 1)
            for i in idxs:
                if 0 <= i < 256:
                    out[i] = value
        return out
    raise ValueError(f"Unsupported helper in material expression: {arg[:60]!r} ({source})")


def parse_material_text(text: str, source: str = "<text>") -> list[int]:
    """Parse a material table from pasted text or file contents.

    Accepts a JSON/JS numeric array (256+ entries, ``materials:`` key and
    trailing comma tolerated) or a room-script expression chain like
    ``defaultMaterials.map(noUndef).map(replaceMatIndexBy(MATERIAL.BG,..._range(188,195)))``.
    """
    stripped = re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.S)
    for match in re.finditer(r"\[([\s\d,]+)\]", stripped):
        values = [int(v) for v in match.group(1).replace("\n", " ").split(",") if v.strip()]
        if len(values) >= 256:
            return _validate_material_table(values, source)
    start = stripped.find("defaultMaterials")
    if start >= 0:
        table = list(DEFAULT_MATERIALS)
        i = start + len("defaultMaterials")
        while True:
            m = re.match(r"\s*\.\s*map\s*\(", stripped[i:])
            if not m:
                break
            arg_start = i + m.end()
            depth, j = 1, arg_start
            while j < len(stripped) and depth:
                if stripped[j] == "(":
                    depth += 1
                elif stripped[j] == ")":
                    depth -= 1
                j += 1
            if depth:
                raise ValueError(f"Unbalanced parentheses in material expression: {source}")
            table = _apply_map_helper(table, stripped[arg_start:j - 1], source)
            i = j
        return _validate_material_table(table, source)
    raise ValueError(f"No material array or defaultMaterials expression found in: {source}")


def load_material_table(path) -> list[int]:
    """Load a 256-entry material table from a file: JSON array, JS snippet with
    an array, or a room-script ``defaultMaterials.map(...)`` expression."""
    from pathlib import Path as _Path
    path = _Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_material_text(text, str(path))


def material_table_to_js(table: list[int], base: list[int] | None = None) -> str:
    """Render a material table as a paste-ready WLE room-script expression.

    Diffs against ``base`` (default: classic table) and emits
    ``defaultMaterials.map(replaceMatIndexBy(...))`` chains, recognizing the
    common ``noUndef`` (all UNDEF -> ROCK) helper first. Falls back to a plain
    array literal when the table doesn't derive from the default one.
    """
    base = list(base or DEFAULT_MATERIALS)
    table = list(table[:256])

    def diff(work):
        changes: dict[int, list[int]] = {}
        for i in range(256):
            if table[i] != work[i]:
                changes.setdefault(table[i], []).append(i)
        return changes

    candidates = [("defaultMaterials", diff(base))]
    no_undef = [MATERIAL["ROCK"] if m == MATERIAL["UNDEF"] else m for m in base]
    candidates.append(("defaultMaterials.map(noUndef)", diff(no_undef)))
    expr, changes = min(candidates,
                        key=lambda c: sum(len(v) for v in c[1].values()))
    if sum(len(v) for v in changes.values()) > 64:
        return "[" + ",".join(str(v) for v in table) + "]"
    for mat_value, idxs in sorted(changes.items()):
        name = MATERIAL_NAMES.get(mat_value, str(mat_value))
        mat_ref = f"MATERIAL.{name}" if mat_value in MATERIAL_NAMES else str(mat_value)
        expr += f".map(replaceMatIndexBy({mat_ref},{_format_index_args(idxs)}))"
    return expr


def _format_index_args(idxs: list[int]) -> str:
    """Render indices the room-script way, folding runs of 3+ into ..._range()."""
    parts = []
    i = 0
    while i < len(idxs):
        j = i
        while j + 1 < len(idxs) and idxs[j + 1] == idxs[j] + 1:
            j += 1
        if j - i >= 2:
            parts.append(f"..._range({idxs[i]},{idxs[j]})")
        else:
            parts.extend(str(v) for v in idxs[i:j + 1])
        i = j + 1
    return ",".join(parts)
