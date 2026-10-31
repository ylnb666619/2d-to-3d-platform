"""Extrude wall areas into a massing solid and write a STEP.

The kernel is build123d over OCCT. On this machine ``import build123d`` fails
unless ``import cadgen`` runs first: cadgen installs a guard for a system font
that the font parser cannot read. The import below therefore tries cadgen and,
when it is absent, still proceeds — the guard is only needed where the broken
font exists.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shapely.geometry import Polygon

_HEIGHT_PATTERNS = (
    re.compile(r"层高\s*[:：=]?\s*(\d{1,4}(?:\.\d+)?)"),
    re.compile(r"[Hh]\s*[:：=]\s*(\d{1,4}(?:\.\d+)?)"),
    re.compile(r"标高差\s*[:：=]?\s*(\d{1,4}(?:\.\d+)?)"),
)
_PLAUSIBLE_HEIGHT = (2000.0, 8000.0)

# An elevation label: three decimals in metres, optionally signed, with AutoCAD's
# %%P plus/minus code. "±0.000", "-0.450", "15.180", "%%P0.000".
_ELEVATION_RE = re.compile(r"(?<!\d)([±+\-]?)\s?(\d{1,3}\.\d{3})(?!\d)")
_STOREY_RANGE_M = (2.2, 6.0)


@dataclass
class HeightReading:
    value_mm: float
    source: str
    evidence: str = ""
    elevations_mm: list[float] | None = None
    first_floor_mm: float | None = None
    storeys: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "height": self.value_mm,
            "source": self.source,
            "evidence": self.evidence,
            "elevations": self.elevations_mm,
            "firstFloor": self.first_floor_mm,
            "storeys": self.storeys,
        }


def _normalize_text(text: str) -> str:
    return (
        str(text)
        .replace("%%P", "±")
        .replace("%%p", "±")
        .replace("％％", "%%")
        .strip()
    )


def read_elevations(texts: list[Any]) -> list[float]:
    """Elevation labels in millimetres, sorted, deduplicated."""
    values: set[float] = set()
    for item in texts:
        text = _normalize_text(getattr(item, "text", "") or "")
        for sign, number in _ELEVATION_RE.findall(text):
            try:
                metres = float(number)
            except ValueError:
                continue
            if sign.strip() in {"-", "−"}:
                metres = -metres
            if -10.0 <= metres <= 300.0:
                values.add(round(metres * 1000.0, 1))
    return sorted(values)


def read_height(
    texts: list[Any],
    *,
    default_mm: float = 3000.0,
    override_mm: float | None = None,
) -> HeightReading:
    """Storey height from the drawing: explicit text first, elevations second.

    A 建施图 rarely says "层高 3.600" in the plan; it stacks elevation labels on
    the section and elevation drawings. The regular step between those labels
    is the storey height, and it is reported with the labels that produced it.
    """
    if override_mm is not None:
        return HeightReading(value_mm=float(override_mm), source="命令行")

    for item in texts:
        text = _normalize_text(getattr(item, "text", "") or "")
        for pattern in _HEIGHT_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            try:
                value = float(match.group(1))
            except ValueError:
                continue
            if value < 100.0:  # written in metres, as in "层高 3.300"
                value *= 1000.0
            if _PLAUSIBLE_HEIGHT[0] <= value <= _PLAUSIBLE_HEIGHT[1]:
                return HeightReading(
                    value_mm=value,
                    source="文字",
                    evidence=text.strip(),
                )

    elevations = read_elevations(texts)
    step, votes = _modal_storey(elevations)
    if step and votes >= 2:
        ground = min(elevations, key=abs) if elevations else None
        chain = _best_chain(elevations, step, ground)
        if len(chain) < 2:
            chain = _progression(elevations, step)
        first_floor = chain[1] - chain[0] if len(chain) > 1 else None
        evidence = " → ".join(f"{value / 1000:.3f}" for value in chain[:10])
        evidence += f"（步距 {step / 1000:.3f}m，{votes} 组标高印证）"
        return HeightReading(
            value_mm=float(step),
            source="标高文字",
            evidence=evidence,
            elevations_mm=chain,
            first_floor_mm=first_floor,
            storeys=max(0, len(chain) - 1),
        )

    return HeightReading(value_mm=float(default_mm), source="默认值")


def _best_chain(
    elevations: list[float],
    step: float,
    ground: float | None,
    tolerance: float = 150.0,
) -> list[float]:
    """The longest storey chain anchored on the ground level.

    The first storey may differ from the rest (a taller ground floor is the
    norm in schools and offices), so the first gap is free within the plausible
    range and only the following gaps must match the modal step.
    """
    starts = [ground] if ground is not None else elevations
    best: list[float] = []
    for start in starts:
        for first in elevations:
            if first <= start:
                continue
            gap = first - start
            if not (_STOREY_RANGE_M[0] * 1000 <= gap <= _STOREY_RANGE_M[1] * 1000):
                continue
            sequence = [start, first]
            current = first
            while True:
                following = [
                    value
                    for value in elevations
                    if current < value and abs(value - (current + step)) <= tolerance
                ]
                if not following:
                    break
                current = min(following, key=lambda value: abs(value - (current + step)))
                sequence.append(current)
            if len(sequence) > len(best):
                best = sequence
    return best


def _modal_storey(elevations: list[float]) -> tuple[float | None, int]:
    """The most common gap between two levels, in 50 mm buckets.

    Pairwise rather than consecutive: a drawing carries sill and parapet levels
    between the storeys, and consecutive gaps would lock onto those.
    """
    counts: dict[float, int] = {}
    for index, first in enumerate(elevations):
        for second in elevations[index + 1:]:
            gap = second - first
            if _STOREY_RANGE_M[0] * 1000 <= gap <= _STOREY_RANGE_M[1] * 1000:
                key = round(gap / 50.0) * 50.0
                counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None, 0
    step, votes = max(counts.items(), key=lambda pair: pair[1])
    return step, votes


def _progression(elevations: list[float], step: float, tolerance: float = 150.0) -> list[float]:
    """The longest run of levels spaced ``step`` apart."""
    best: list[float] = []
    for start in elevations:
        sequence = [start]
        current = start
        while True:
            following = [value for value in elevations if current < value and abs(value - (current + step)) <= tolerance]
            if not following:
                break
            current = min(following)
            sequence.append(current)
        if len(sequence) > len(best):
            best = sequence
    return best


def find_height(texts: list[Any]) -> tuple[float | None, str]:
    """Backwards-compatible tuple form: ``(height, source)``."""
    reading = read_height(texts, default_mm=0.0)
    if reading.source == "默认值":
        return None, "default"
    return reading.value_mm, reading.source


@dataclass
class MassingOptions:
    height_mm: float | None = None
    default_height_mm: float = 3000.0
    scale_to_mm: float | None = None


def _kernel():
    try:  # pragma: no cover - depends on the host installation
        import cadgen  # noqa: F401  installs build123d's font guard
    except Exception:  # noqa: BLE001 - cadgen is optional for this tool
        pass
    import build123d as bd

    return bd


def _to_mm(polygon: Polygon, scale: float) -> Polygon:
    if scale == 1.0:
        return polygon
    return Polygon([(x * scale, y * scale) for x, y in polygon.exterior.coords],
                   [[(x * scale, y * scale) for x, y in ring.coords] for ring in polygon.interiors])


def _solid_from_polygon(bd: Any, polygon: Polygon, height: float):
    outer = [bd.Vector(x, y, 0) for x, y in polygon.exterior.coords]
    if len(outer) < 3:
        return None
    wire = bd.Wire.make_polygon(outer, close=True)
    face = bd.Face(wire)
    # Extrude up, never along the face normal: a clockwise wire's normal points
    # down, which would put the whole building below the ground plane.
    body = bd.extrude(face, amount=height, dir=(0, 0, 1))
    for ring in polygon.interiors:
        inner_points = [bd.Vector(x, y, 0) for x, y in ring.coords]
        if len(inner_points) < 3:
            continue
        inner_wire = bd.Wire.make_polygon(inner_points, close=True)
        cutter = bd.extrude(bd.Face(inner_wire), amount=height * 1.2, dir=(0, 0, 1))
        body = body - cutter
    return body


def build_step(
    polygons: list[Polygon],
    out_path: str | Path,
    *,
    height_mm: float,
    scale_to_mm: float = 1.0,
) -> dict[str, Any]:
    """Extrude every wall area and write a STEP; return what was built."""
    if not polygons:
        raise ValueError("没有墙体面可以拉伸。")

    bd = _kernel()
    solids = []
    failed = 0
    for polygon in polygons:
        scaled = _to_mm(polygon, scale_to_mm)
        try:
            solid = _solid_from_polygon(bd, scaled, height_mm)
        except Exception:  # noqa: BLE001 - one bad polygon must not lose the rest
            failed += 1
            continue
        if solid is not None and getattr(solid, "volume", 0) > 0:
            solids.append(solid)
        else:
            failed += 1
    if not solids:
        raise ValueError("所有墙体面都拉伸失败，请检查轮廓是否闭合。")

    compound = bd.Compound(children=solids)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    bd.export_step(compound, str(target))
    return {
        "path": str(target),
        "solids": len(solids),
        "failed": failed,
        "height": height_mm,
        "volume": round(sum(float(solid.volume) for solid in solids), 1),
        "bbox": _bbox(compound),
    }


def build_step_levels(
    groups: list[list[Polygon]],
    out_path: str | Path,
    *,
    floor_height_mm: float,
    scale_to_mm: float = 1.0,
    base_elevations: list[float] | None = None,
) -> dict[str, Any]:
    """Stack one wall group per storey, each lifted to its own elevation.

    ``groups`` is ordered bottom-up: index 0 is the ground floor. When
    ``base_elevations`` is given (one per group), each storey starts at that
    level and rises to the next, so a taller ground floor is honoured.
    """
    if not groups or not any(groups):
        raise ValueError("没有墙体面可以拉伸。")

    def level_base(index: int) -> float:
        if base_elevations and index < len(base_elevations):
            return float(base_elevations[index])
        return index * floor_height_mm

    def level_height(index: int) -> float:
        if base_elevations and index + 1 < len(base_elevations):
            return float(base_elevations[index + 1] - base_elevations[index])
        return floor_height_mm

    bd = _kernel()
    solids = []
    failed = 0
    per_level = []
    for level, polygons in enumerate(groups):
        base = level_base(level)
        height_here = level_height(level)
        level_solids = 0
        level_area = 0.0
        for polygon in polygons:
            scaled = _to_mm(polygon, scale_to_mm)
            try:
                solid = _solid_from_polygon(bd, scaled, height_here)
            except Exception:  # noqa: BLE001
                failed += 1
                continue
            if solid is None or getattr(solid, "volume", 0) <= 0:
                failed += 1
                continue
            if base:
                solid = bd.Pos(0, 0, base) * solid
            solids.append(solid)
            level_solids += 1
            level_area += scaled.area
        per_level.append(
            {
                "level": level,
                "elevation": base,
                "height": height_here,
                "solids": level_solids,
                "area": round(level_area, 1),
            }
        )

    if not solids:
        raise ValueError("所有墙体面都拉伸失败，请检查轮廓是否闭合。")

    compound = bd.Compound(children=solids)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    bd.export_step(compound, str(target))
    return {
        "path": str(target),
        "solids": len(solids),
        "failed": failed,
        "height": floor_height_mm,
        "volume": round(sum(float(solid.volume) for solid in solids), 1),
        "bbox": _bbox(compound),
        "levels": per_level,
    }


def _bbox(shape: Any) -> list[float] | None:
    try:
        box = shape.bounding_box()
        return [
            round(float(box.min.X), 2),
            round(float(box.min.Y), 2),
            round(float(box.min.Z), 2),
            round(float(box.max.X), 2),
            round(float(box.max.Y), 2),
            round(float(box.max.Z), 2),
        ]
    except Exception:  # noqa: BLE001
        return None


def write_sidecar(path: str | Path, payload: dict[str, Any]) -> str:
    target = Path(path).with_suffix(".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(target)
