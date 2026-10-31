"""One sentence in, a building block out.

The front end offers "一句话生成建模" next to the drawing reader, and it has to
answer without asking a model for code: a drawing reader is a local tool with
no account attached. So the sentence is read the way a 建施图 is read -- a
handful of numbers with names around them -- and what comes out is a massing
block: floors, slabs, a perimeter wall and a column grid.

Being honest about what was understood is part of the feature. The result
carries the spec it parsed, so "3 层，层高 3.6，40×18" can be checked before the
model is used for anything.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from shapely.geometry import Polygon, box

_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_NUM = r"(\d+(?:\.\d+)?)"


@dataclass
class Spec:
    storeys: int = 3
    height: float = 3600.0      # storey height, mm
    length: float = 40_000.0    # mm
    width: float = 18_000.0     # mm
    bay: float = 7_200.0        # column spacing, mm
    wall: float = 200.0
    slab: float = 120.0
    column: float = 500.0
    said: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["lengthM"] = round(self.length / 1000, 2)
        data["widthM"] = round(self.width / 1000, 2)
        data["heightM"] = round(self.height / 1000, 2)
        data["bayM"] = round(self.bay / 1000, 2)
        return data

    def sentence(self) -> str:
        return (
            f"{self.storeys} 层，层高 {self.height / 1000:g} m，"
            f"{self.length / 1000:g} × {self.width / 1000:g} m，柱距 {self.bay / 1000:g} m"
        )


def _mm(value: float, unit: str | None) -> float:
    """A number with a unit, or a number that reads like one.

    Architects write 3.6 for a height and 3600 for the same height; anything
    under 100 is metres, anything over is millimetres. The unit wins when the
    sentence gives one.
    """
    if unit:
        unit = unit.lower()
        if unit in {"米", "m"}:
            return value * 1000.0
        return value
    return value * 1000.0 if value < 100 else value


def _find(pattern: str, text: str) -> re.Match[str] | None:
    return re.search(pattern, text)


def parse(text: str) -> Spec:
    spec = Spec(said=text.strip())
    lowered = text.replace("　", " ")

    for word, count in _CN_DIGITS.items():
        lowered = re.sub(rf"{word}\s*层", f"{count}层", lowered)
    lowered = re.sub(r"单\s*层", "1层", lowered)
    lowered = re.sub(r"双\s*层", "2层", lowered)

    match = _find(rf"(\d+)\s*层(?!高)", lowered)
    if match:
        spec.storeys = max(1, min(60, int(match.group(1))))

    match = _find(rf"层高\s*{_NUM}\s*(米|m|mm|毫米)?", lowered)
    if match:
        spec.height = _mm(float(match.group(1)), match.group(2))

    # 40×18, 40x18, 40乘18
    match = _find(rf"{_NUM}\s*米?\s*[×xX*乘]\s*{_NUM}", lowered)
    if match:
        spec.length = _mm(float(match.group(1)), None)
        spec.width = _mm(float(match.group(2)), None)
    else:
        match = _find(rf"长\s*{_NUM}\s*(米|m|mm)?", lowered)
        if match:
            spec.length = _mm(float(match.group(1)), match.group(2))
        match = _find(rf"(?:宽|进深|纵深)\s*{_NUM}\s*(米|m|mm)?", lowered)
        if match:
            spec.width = _mm(float(match.group(1)), match.group(2))

    match = _find(rf"(?:开间|柱距|柱网|跨度)\s*{_NUM}\s*(米|m|mm)?", lowered)
    if match:
        spec.bay = _mm(float(match.group(1)), match.group(2))

    spec.wall = min(spec.wall, max(100.0, spec.length / 40))
    spec.bay = max(1200.0, min(spec.bay, spec.length / 2))
    return spec


def _column_grid(spec: Spec) -> list[Polygon]:
    """Columns on a bay grid, pulled in from the corners by half a column."""
    half = spec.column / 2
    xs = []
    x = half + max(0.0, (spec.bay - (spec.length % spec.bay)) / 2)
    while x + half <= spec.length + 0.5:
        xs.append(x)
        x += spec.bay
    if not xs:
        xs = [spec.length / 2]
    ys = []
    y = half + max(0.0, (spec.bay - (spec.width % spec.bay)) / 2)
    while y + half <= spec.width + 0.5:
        ys.append(y)
        y += spec.bay
    if not ys:
        ys = [spec.width / 2]
    columns = []
    for cx in xs:
        for cy in ys:
            columns.append(box(cx - half, cy - half, cx + half, cy + half))
    return columns


def pieces(spec: Spec) -> list[tuple[str, Polygon, float, float]]:
    """``(name, footprint, z0, z1)`` for every part of the block."""
    outline = box(0.0, 0.0, spec.length, spec.width)
    ring = outline.difference(outline.buffer(-spec.wall, join_style="mitre"))
    result: list[tuple[str, Polygon, float, float]] = []
    for floor in range(spec.storeys):
        base = floor * spec.height
        result.append((f"{floor + 1}层楼板", outline, base - spec.slab, base))
        if not ring.is_empty:
            result.append((f"{floor + 1}层外墙", ring, base, base + spec.height - spec.slab))
        for index, column in enumerate(_column_grid(spec), start=1):
            result.append((f"{floor + 1}层柱-{index}", column, base, base + spec.height - spec.slab))
    top = spec.storeys * spec.height
    result.append(("屋面板", outline, top, top + spec.slab * 1.25))
    return result


def scene(spec: Spec) -> dict[str, Any]:
    """The same block as the browser's scene format, so it can be looked at
    without a kernel and without leaving this app."""
    items: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for name, footprint, z0, z1 in pieces(spec):
        if z1 - z0 <= 1.0 or footprint.is_empty:
            continue
        if "楼板" in name or "屋面" in name:
            category = "slab" if "楼板" in name else "roof"
        elif "外墙" in name:
            category = "wall"
        else:
            category = "column"
        rings = [[[x / 1000.0, y / 1000.0] for x, y in footprint.exterior.coords[:-1]]]
        for interior in footprint.interiors:
            rings.append([[x / 1000.0, y / 1000.0] for x, y in interior.coords[:-1]])
        for ring in rings:
            xs.extend(point[0] for point in ring)
            ys.extend(point[1] for point in ring)
        items.append(
            {
                "c": category,
                "f": min(spec.storeys, int(name.split("层")[0]) if "层" in name else spec.storeys),
                "n": name,
                "r": rings,
                "z": [round(z0 / 1000.0, 3), round(z1 / 1000.0, 3)],
                "a": round(footprint.area / 1e6, 2),
                "v": round(footprint.area * (z1 - z0) / 1e9, 3),
            }
        )
        counts[category] = counts.get(category, 0) + 1
        zs.extend([z0 / 1000.0, z1 / 1000.0])

    floors = [
        {
            "n": floor + 1,
            "z": round(floor * spec.height / 1000.0, 3),
            "h": round(spec.height / 1000.0, 3),
            "title": f"{floor + 1}层",
            "align": "",
            "score": 1.0,
            "walls": 1,
            "columns": 0,
            "openings": 0,
            "stairs": 0,
            "rooms": 0,
        }
        for floor in range(spec.storeys)
    ]
    order = ["wall", "column", "slab", "roof"]
    return {
        "unit": "m",
        "floors": floors,
        "items": items,
        "counts": counts,
        "categories": [category for category in order if category in counts],
        "bounds": {
            "x": [round(min(xs), 3), round(max(xs), 3)],
            "y": [round(min(ys), 3), round(max(ys), 3)],
            "z": [round(min(zs), 3), round(max(zs), 3)],
        },
        "notes": [spec.sentence()],
    }


def build(spec: Spec, step_path: str) -> dict[str, Any]:
    """The STEP file, and the numbers that describe it."""
    from .massing import _kernel, _solid_from_polygon

    bd = _kernel()
    solids = []
    for name, footprint, z0, z1 in pieces(spec):
        if z1 - z0 <= 1.0 or footprint.is_empty:
            continue
        solid = _solid_from_polygon(bd, footprint, z1 - z0)
        if solid is None or getattr(solid, "volume", 0) <= 0:
            continue
        body = bd.Pos(0, 0, z0) * solid
        body.label = name
        solids.append(body)
    compound = bd.Compound(children=solids)
    bd.export_step(compound, str(step_path))
    return {
        "ok": True,
        "spec": spec.to_dict(),
        "sentence": spec.sentence(),
        "step": str(step_path),
        "solids": len(solids),
        "volume": round(sum(float(solid.volume) for solid in solids) / 1e9, 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="massing.preset", description="一句话生成体块")
    parser.add_argument("text", help="一句话描述，例如「3 层，层高 3.6，40×18，柱距 7.2」")
    parser.add_argument("--step", required=True, help="输出的 STEP 路径")
    parser.add_argument("--json", help="把结果写成 JSON")
    args = parser.parse_args(argv)

    for stream in (__import__("sys").stdout, __import__("sys").stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass

    spec = parse(args.text)
    Path(args.step).parent.mkdir(parents=True, exist_ok=True)
    result = build(spec, args.step)
    # The browser's copy: same block, no kernel needed to look at it.
    scene_file = Path(args.step).with_suffix(".scene.json")
    try:
        scene_file.write_text(
            json.dumps(scene(spec), ensure_ascii=False), encoding="utf-8"
        )
        result["scene"] = str(scene_file)
    except Exception:  # noqa: BLE001 - the STEP is the product, the scene is a view
        pass
    payload = json.dumps(result, ensure_ascii=False)
    if args.json:
        Path(args.json).write_text(payload, encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
