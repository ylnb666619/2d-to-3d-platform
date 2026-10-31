"""The intermediate representation: what the tool understands about a drawing.

Everything here is JSON-serialisable through ``to_dict``. The point of the
intermediate model is that a person can open it, see which lines were called a
wall, and fix the rule — rather than only seeing a wrong model at the end.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Point = tuple[float, float]
Bounds = tuple[float, float, float, float]  # minx, miny, maxx, maxy


def _pt(p: Point | None) -> list[float] | None:
    return None if p is None else [round(p[0], 4), round(p[1], 4)]


def _bounds(b: Bounds | None) -> list[float] | None:
    return None if b is None else [round(v, 4) for v in b]


@dataclass(frozen=True)
class Units:
    insunits: int
    name: str
    to_mm: float | None

    @property
    def known(self) -> bool:
        return self.to_mm is not None

    def to_dict(self) -> dict[str, Any]:
        return {"insunits": self.insunits, "name": self.name, "toMillimetres": self.to_mm}


@dataclass
class LayerInfo:
    name: str
    color: str | None
    count: int = 0
    kinds: dict[str, int] = field(default_factory=dict)
    role: str = "unknown"
    rule: str = ""
    confidence: str = "none"  # exact | substring | token | none

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "color": self.color,
            "count": self.count,
            "kinds": dict(sorted(self.kinds.items())),
            "role": self.role,
            "rule": self.rule,
            "confidence": self.confidence,
        }


@dataclass
class BlockRef:
    name: str
    handle: str
    layer: str
    insert: Point
    scale: Point
    rotation: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "handle": self.handle,
            "layer": self.layer,
            "insert": _pt(self.insert),
            "scale": [round(self.scale[0], 6), round(self.scale[1], 6)],
            "rotation": round(self.rotation, 4),
        }


@dataclass
class TextItem:
    text: str
    layer: str
    handle: str
    position: Point | None
    height: float
    kind: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "layer": self.layer,
            "handle": self.handle,
            "position": _pt(self.position),
            "height": round(self.height, 3),
            "kind": self.kind,
        }


@dataclass
class DimItem:
    kind: str
    layer: str
    handle: str
    measurement: float | None
    position: Point | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "layer": self.layer,
            "handle": self.handle,
            "measurement": None if self.measurement is None else round(self.measurement, 4),
            "position": _pt(self.position),
        }


@dataclass
class PolylineItem:
    handle: str
    layer: str
    points: list[Point]
    closed: bool
    bulges: list[float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "layer": self.layer,
            "points": [_pt(p) for p in self.points],
            "closed": self.closed,
            "bulges": [round(b, 6) for b in self.bulges],
        }


@dataclass
class Region:
    index: int
    bounds: Bounds
    entity_count: int
    layers: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "bounds": _bounds(self.bounds),
            "entityCount": self.entity_count,
            "layers": dict(sorted(self.layers.items())),
        }


@dataclass
class DocumentInfo:
    path: str
    dxf_version: str
    units: Units
    bounds: Bounds | None
    entity_count: int
    layers: list[LayerInfo]
    blocks: list[BlockRef]
    texts: list[TextItem]
    dimensions: list[DimItem]
    polylines: list[PolylineItem]
    regions: list[Region]
    entity_types: dict[str, int]
    xrefs: list[str]
    warnings: list[str] = field(default_factory=list)

    def layer(self, name: str) -> LayerInfo | None:
        for layer in self.layers:
            if layer.name == name:
                return layer
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "path": self.path,
            "dxfVersion": self.dxf_version,
            "units": self.units.to_dict(),
            "bounds": _bounds(self.bounds),
            "entityCount": self.entity_count,
            "entityTypes": dict(sorted(self.entity_types.items())),
            "layers": [layer.to_dict() for layer in self.layers],
            "blocks": [block.to_dict() for block in self.blocks],
            "texts": [text.to_dict() for text in self.texts],
            "dimensions": [dim.to_dict() for dim in self.dimensions],
            "polylines": [pline.to_dict() for pline in self.polylines],
            "regions": [region.to_dict() for region in self.regions],
            "xrefs": list(self.xrefs),
            "warnings": list(self.warnings),
        }


@dataclass
class WallSegment:
    """One wall run: a centreline, a thickness, and where it came from."""

    start: Point
    end: Point
    thickness: float
    source: str  # "closed" | "pairs" | "hatch"
    layer: str
    handles: list[str] = field(default_factory=list)
    suspicious: str | None = None

    @property
    def length(self) -> float:
        (x0, y0), (x1, y1) = self.start, self.end
        return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5

    @property
    def midpoint(self) -> Point:
        return ((self.start[0] + self.end[0]) / 2, (self.start[1] + self.end[1]) / 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": _pt(self.start),
            "end": _pt(self.end),
            "thickness": round(self.thickness, 3),
            "length": round(self.length, 3),
            "source": self.source,
            "layer": self.layer,
            "handles": list(self.handles),
            "suspicious": self.suspicious,
        }


@dataclass
class WallResult:
    segments: list[WallSegment]
    polygons: list[Any] = field(default_factory=list)  # shapely Polygons, not serialised
    # Column footprints, kept apart from the walls so IFC can write IfcColumn
    # rather than treating a column as a very short wall. They are also part of
    # ``polygons``, because the massing extrudes both.
    columns: list[Any] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "count": len(self.segments),
            "segments": [segment.to_dict() for segment in self.segments],
            "stats": self.stats,
            "warnings": list(self.warnings),
        }
